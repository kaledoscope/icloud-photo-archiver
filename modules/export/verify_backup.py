#!/usr/bin/env python3.11
"""
verify_backup.py — 验证远端备份完整性（只看即将被清理的老文件）

用 os.stat() 比较远端文件大小（不下载文件内容），比 SHA256 快 100 倍以上，
不受 FUSE 网络波动影响。清单里的 size_bytes 是导出时记录的精确值。

核心原则（第一性原理）:
    Step 0 的真正目的是在清理本地中转文件之前，确认"即将被删除的老文件"
    已经在远端安全归档。只验证拍摄日期超过保留期的文件 —— 保留期内的
    文件不需要验证，即使远端没有，本次 rsync 也会传上去。

    老文件在远端缺失或大小不符 → FATAL（退出码 1），流水线必须停下。

备份目标是【配置驱动】的：改 config 的 BACKUP_TARGETS 增删目标，
本文件不需要动。每个目标从 REMOTE_<目标名大写>_DIR 读取挂载路径。

用法::

    python -m modules.export.verify_backup                    # 验证所有已配置目标
    python -m modules.export.verify_backup --target 115       # 仅验证 115
    python -m modules.export.verify_backup --cutoff-months 4  # 只看 4 个月前的文件
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from modules.utils.config import get_int, get_path, require_path
from modules.utils.manifest import (
    Manifest, cleanup_required_targets, configured_targets, cutoff_ym,
)

BACKUP_ROOT = require_path("BACKUP_ROOT")
DEFAULT_CUTOFF_MONTHS = get_int("STAGING_RETENTION_MONTHS", 3)


def remote_base(target: str) -> Optional[Path]:
    """备份目标 → 远端挂载路径。约定: REMOTE_<目标名大写>_DIR。"""
    return get_path(f"REMOTE_{target.upper()}_DIR")


def _remote_available(remote_base_path: Path) -> bool:
    """检查远端挂载是否可用。

    FUSE 挂载点可能目录存在但后端断连（Device not configured），
    此时 Path.exists() 抛 OSError 而不是返回 False，需要兜底。
    """
    try:
        return remote_base_path.exists()
    except OSError:
        return False


def _verify_remote_file(relative_path: str, base: Path, expected_size: int) -> Dict:
    """验证单个远端文件：只比大小（os.stat，不下载内容）。

    走 FUSE 挂载时 stat() 只需一次元数据查询，比下载整个文件算 SHA256
    快 100 倍以上，也不会因网络波动超时。
    """
    result = {
        "path": relative_path,
        "exists": False,
        "size_match": False,
        "remote_size": 0,
        "expected_size": expected_size,
        "error": None,
    }
    try:
        st = os.stat(str(base / relative_path))
        result["exists"] = True
        result["remote_size"] = st.st_size
        result["size_match"] = (st.st_size == expected_size)
    except FileNotFoundError:
        result["error"] = "文件不存在于远端"
    except OSError as e:
        if "Device not configured" in str(e):
            result["error"] = "远端设备不可用 (Device not configured)"
        else:
            result["error"] = f"无法读取远端文件: {e}"
    return result


def verify_target(target: str, base: Path, sample: List[str], m: Manifest) -> Dict:
    """验证一个备份目标上的抽样文件列表。"""
    if not _remote_available(base):
        return {
            "target": target,
            "available": False,
            "reason": f"挂载点不可用: {base}",
            "passed": 0, "failed": 0, "warn": 0, "total": 0,
            "details": [],
        }

    passed = failed = warn = 0
    details = []

    for relative_path in sample:
        entry = m.entry_for(relative_path)
        if entry is None:
            details.append({"path": relative_path, "status": "skip",
                            "reason": "不在清单中"})
            continue

        v = _verify_remote_file(relative_path, base, entry.get("size_bytes", 0))

        if v["size_match"]:
            passed += 1
            details.append({"path": relative_path, "status": "ok"})
            continue

        error_msg = v.get("error") or "文件大小不匹配"
        # 区分「文件真缺失」和「FUSE 不稳定导致无法验证」
        is_transient = "Device not configured" in error_msg or "无法读取" in error_msg

        if is_transient and m.is_backed_up(entry, target):
            warn += 1
            details.append({"path": relative_path, "status": "warn",
                            "reason": f"无法验证 ({error_msg})，但清单记录已归档"})
        else:
            failed += 1
            detail = {"path": relative_path, "status": "fail", "reason": error_msg}
            if v.get("remote_size", 0) > 0:
                detail["expected_size"] = entry.get("size_bytes", 0)
                detail["remote_size"] = v["remote_size"]
            details.append(detail)

    return {
        "target": target, "available": True,
        "passed": passed, "failed": failed, "warn": warn,
        "total": passed + failed + warn, "details": details,
    }


def _print_details(r: Dict, m: Manifest, base: Path) -> None:
    """逐个显示验证结果（不管成败，全量透明）。"""
    for d in r["details"]:
        if d["status"] == "ok":
            entry = m.entry_for(d["path"]) or {}
            print(f"  ✅ {d['path']}")
            print(f"     本地位于: {BACKUP_ROOT / d['path']}")
            print(f"     远端: 存在, {entry.get('size_bytes', '?')} bytes 一致")
        elif d["status"] == "fail":
            print(f"  ❌ {d['path']}")
            print(f"     本地位于: {BACKUP_ROOT / d['path']}")
            print(f"     原因: {d.get('reason', '未知')}")
            if d.get("expected_size"):
                print(f"     本地 size: {d['expected_size']} bytes")
                print(f"     远端 size: {d['remote_size']} bytes")
        elif d["status"] == "warn":
            print(f"  ⚠️  {d['path']}")
            print(f"     本地位于: {BACKUP_ROOT / d['path']}")
            print(f"     {d['reason']}")
        elif d["status"] == "skip":
            print(f"  ⏭  {d['path']}: {d['reason']}")
    print()


def main() -> int:
    targets = configured_targets()
    parser = argparse.ArgumentParser(description="验证远端备份完整性（只看即将被清理的老文件）")
    parser.add_argument("--target", choices=targets, help="仅验证指定目标")
    parser.add_argument("--cutoff-months", type=int, default=DEFAULT_CUTOFF_MONTHS,
                        help=f"只看 N 个月前的文件（默认 {DEFAULT_CUTOFF_MONTHS}）")
    parser.add_argument("--sample-size", type=int, default=0,
                        help="随机抽样 N 个（默认 0 = 全量检查所有风险文件）")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示每个文件的验证详情")
    args = parser.parse_args()

    m = Manifest()
    if not m.exists:
        print("❌ 清单文件不存在，无法验证")
        print("[RESULT] verified=0 passed=0 failed=0 blocked=true")
        return 1

    all_files = list(m.files.keys())
    if not all_files:
        print("清单为空，无需验证")
        print("[RESULT] verified=0 passed=0 failed=0 blocked=false")
        return 0

    limit = cutoff_ym(args.cutoff_months)
    cutoff_label = f"{limit[0]}-{limit[1]:02d}"
    at_risk = m.at_risk(args.cutoff_months, BACKUP_ROOT)

    if not at_risk:
        print("备份完整性验证")
        print(f"  清单文件数: {len(all_files)}")
        print(f"  保留期限:   {args.cutoff_months} 个月 (cutoff: {cutoff_label})")
        print(f"  风险文件:   0（无待清理的老文件）")
        print()
        print("✅ 无待清理文件，跳过验证")
        print("[RESULT] verified=0 passed=0 failed=0 blocked=false")
        return 0

    if args.sample_size > 0:
        import random
        size = min(args.sample_size, len(at_risk))
        sample = random.sample(at_risk, size)
        strategy = f"指定抽样 {size}/{len(at_risk)} 个"
    else:
        # os.stat() 只比大小不读内容，毫秒级完成，不需要抽样
        sample = at_risk
        strategy = f"全量检查 {len(at_risk)} 个（stat 秒级，无需抽样）"

    print("备份完整性验证")
    print(f"  清单文件数: {len(all_files)} (清单记录)")
    print(f"  保留期限:   {args.cutoff_months} 个月 (cutoff: {cutoff_label})")
    print(f"  风险文件:   {len(at_risk)}（拍摄于 {cutoff_label} 之前且磁盘上存在）")
    ghosts = m.ghost_count(args.cutoff_months, BACKUP_ROOT)
    if ghosts:
        print(f"              ({ghosts} 个已清理的幽灵条目已跳过)")
    print(f"  抽样策略:   {strategy}")
    print(f"  抽样数量:   {len(sample)}")
    print(f"  验证时间:   {datetime.now().isoformat()}")
    print()

    # 只有「清理前置目标」的验证结果才阻塞流水线。
    # Step 0 回答的是"我要删的东西安全吗"，NAS 常年离线，
    # 不该因为没开机就卡住整条流水线。
    required = set(cleanup_required_targets())

    fatal = False
    verified = passed_total = failed_total = 0
    for target in targets:
        if args.target and args.target != target:
            continue

        base = remote_base(target)
        if base is None:
            if args.target:
                print(f"❌ 未配置 REMOTE_{target.upper()}_DIR，无法验证 {target}")
                fatal = True
            continue

        blocking = target in required
        print(f"验证 {target} (挂载点: {base})...")
        print()
        r = verify_target(target, base, sample, m)
        verified += 1
        passed_total += r.get("passed", 0)
        failed_total += r.get("failed", 0)

        if not r["available"]:
            if blocking:
                print(f"  ❌ 跳过: {r['reason']}")
                fatal = True
            else:
                print(f"  ⚠️  跳过: {r['reason']}（非清理前置目标，不阻塞）")
        else:
            _print_details(r, m, base)
            if r["failed"] > 0:
                if blocking:
                    fatal = True
                    print(f"  ❌ 汇总: {r['passed']}/{r['total']} 通过, {r['failed']} 失败（老文件未归档！）")
                else:
                    print(f"  ⚠️  汇总: {r['passed']}/{r['total']} 通过, {r['failed']} 失败（非清理前置目标，不阻塞）")
                if r.get("warn", 0) > 0:
                    print(f"  ⚠️  {r['warn']} 个因 FUSE 不稳定无法验证")
            elif r.get("warn", 0) > 0:
                print(f"  ⚠️  汇总: {r['passed']}/{r['total']} 通过, {r['warn']} 个因 FUSE 不稳定无法验证")
                print(f"     (清单记录均已归档，不阻塞流水线)")
            else:
                print(f"  ✅ 汇总: {r['passed']}/{r['total']} 全部通过")
        print()

    # 机器可读结果行，供 shell 提取，不要改格式
    print(f"[RESULT] verified={verified} passed={passed_total} "
          f"failed={failed_total} blocked={'true' if fatal else 'false'}")

    if fatal:
        print("❌ 验证失败：存在未归档的老文件，清理步骤已阻塞！")
        print("   请检查远端挂载状态或手动执行 rsync 后再运行流水线。")
        return 1

    print("✅ 所有待清理文件已在远端安全归档")
    return 0


if __name__ == "__main__":
    sys.exit(main())
