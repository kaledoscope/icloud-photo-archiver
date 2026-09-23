#!/usr/bin/env python3.11
"""
clean_staging.py — 清理本地中转目录里已过期、且确认已归档的月份目录

放在导出和 rsync 之前跑：中转目录瘦下来之后，rsync 不必再扫描历史垃圾。

安全阀（这是本模块存在的全部意义）:
    只有当一个月份目录下的**每一个文件**都在清单里被标记为已备份，
    才允许动它。任何一项未确认 → 整个目录保留。

    宁可留一堆没用的本地文件，也不能删掉唯一一份还没上云的照片。

清理方式由 STAGING_DELETE_MODE 决定:
    trash   移入 ~/.Trash（默认，可恢复）
    delete  直接删除

    注意：trash 模式若移动失败（跨卷、废纸篓不可写），目录会被【保留】
    并计入 failed，不会退化成硬删除。数据安全优先于清理彻底。

用法::

    python -m modules.cleanup.clean_staging
    python -m modules.cleanup.clean_staging --dry-run
    python -m modules.cleanup.clean_staging --months 6

输出最后一行固定为::

    [RESULT] cleaned=N kept=N failed=N

供 shell 提取汇总数据，不要改动这个格式。
"""

import argparse
import calendar
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Tuple

from modules.utils.config import get, get_int, require_path
from modules.utils.manifest import Manifest, cleanup_required_targets

# 中转目录下不属于照片数据的目录，不参与遍历，也永远不会被清理
META_DIRS = {".state", ".logs", ".Trash"}


def cutoff_date(months: int) -> date:
    """N 个月前的今天。与 `date -v-{N}m` 的日历减法语义一致。"""
    today = date.today()
    total = today.year * 12 + (today.month - 1) - months
    year, month = total // 12, (total % 12) + 1
    day = min(today.day, calendar.monthrange(year, month)[1])  # 31 号回退
    return date(year, month, day)


def parse_month_dir(path: Path) -> Optional[Tuple[int, int]]:
    """从 <设备>/<年>/<月> 目录结构里取出年月；不合法返回 None。"""
    try:
        year, month = int(path.parent.name), int(path.name)
    except ValueError:
        return None
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        return None
    return year, month


def find_month_dirs(backup_root: Path) -> List[Path]:
    """列出所有 <设备>/<年>/<月> 目录，按时间升序。"""
    found: List[Path] = []
    if not backup_root.is_dir():
        return found

    for device_dir in sorted(backup_root.iterdir()):
        if not device_dir.is_dir() or device_dir.name in META_DIRS:
            continue
        for year_dir in sorted(device_dir.iterdir()):
            if not year_dir.is_dir():
                continue
            for month_dir in sorted(year_dir.iterdir()):
                if month_dir.is_dir() and parse_month_dir(month_dir):
                    found.append(month_dir)
    return found


def dir_is_empty(path: Path) -> bool:
    """目录里除了 .DS_Store 之外什么都没有，视为空。"""
    for child in path.iterdir():
        if child.name != ".DS_Store":
            return False
    return True


def move_to_trash(path: Path) -> bool:
    """把目录移入 ~/.Trash。失败返回 False（调用方保留原目录）。"""
    trash = Path.home() / ".Trash"
    try:
        trash.mkdir(exist_ok=True)
    except OSError:
        return False

    dest = trash / path.name
    if dest.exists():
        dest = trash / f"{path.name}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    try:
        shutil.move(str(path), str(dest))
        return True
    except OSError:
        return False


def prune_empty_dirs(backup_root: Path) -> int:
    """自底向上删掉空目录（清理后的残留：空月份 → 空年份 → 空设备）。

    只删空目录，且不碰 .state/.logs/.Trash。
    """
    removed = 0
    if not backup_root.is_dir():
        return removed

    # 反复扫，直到一轮下来没有任何变化 —— 空目录可能自底向上连锁出现
    changed = True
    while changed:
        changed = False
        for path in sorted(backup_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if not path.is_dir() or path.name in META_DIRS:
                continue
            try:
                if any(path.iterdir()):
                    continue
                path.rmdir()
                removed += 1
                changed = True
            except OSError:
                continue
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="清理本地中转目录里的过期月份目录")
    parser.add_argument("--months", type=int, default=None,
                        help="保留月数（默认取 config 的 STAGING_RETENTION_MONTHS）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只报告将发生什么，不实际移动/删除")
    args = parser.parse_args()

    backup_root = require_path("BACKUP_ROOT")
    months = args.months if args.months is not None else get_int("STAGING_RETENTION_MONTHS", 3)
    delete_mode = str(get("STAGING_DELETE_MODE", "trash")).strip().lower()
    # 清理前必须确认哪些目标已完成备份（默认只要求 115；NAS 常年离线，
    # 把它设为前置条件会让清理永远不触发）
    require_targets = cleanup_required_targets()

    # 配置成空 = 没有任何目标需要确认备份 = 无条件删除。
    # 这是安全阀的失效模式，必须硬性中止，不能"那就都删吧"。
    if not require_targets:
        print("❌ CLEANUP_REQUIRE_TARGETS 为空 —— 没有任何备份目标需要确认，")
        print("   继续清理等于无条件删除本地文件。已中止。")
        print('   请在 config/config.env 中设置，例如 CLEANUP_REQUIRE_TARGETS="115"')
        print("[RESULT] cleaned=0 kept=0 failed=1")
        return 1

    cutoff = cutoff_date(months)
    print(f"保留期限: {months} 个月 (截止 {cutoff.isoformat()}，早于此日期的月份目录才会被清理)")
    print(f"清理方式: {delete_mode}  前置备份确认: {', '.join(require_targets) or '（无）'}")
    if args.dry_run:
        print("[DRY RUN] 不会实际移动或删除任何文件")
    print()

    manifest = Manifest()
    if not manifest.exists:
        print("清单文件不存在，跳过清理（首次运行无历史文件）")
        print("[RESULT] cleaned=0 kept=0 failed=0")
        return 0

    month_dirs = find_month_dirs(backup_root)
    if not month_dirs:
        print("没有找到任何 <设备>/<年>/<月> 目录")
        print("[RESULT] cleaned=0 kept=0 failed=0")
        return 0

    cleaned = kept = failed = 0
    for month_dir in month_dirs:
        ym = parse_month_dir(month_dir)
        assert ym is not None  # find_month_dirs 已经校验过
        label = "/".join(month_dir.relative_to(backup_root).parts)
        display = f"{ym[0]}年{ym[1]:02d}月"

        if date(ym[0], ym[1], 1) >= cutoff:
            continue  # 还在窗口内

        if dir_is_empty(month_dir):
            # 空目录不是数据，直接算作可清理，不必等备份确认
            if args.dry_run:
                print(f"  🗑  {display}  {label}  (空目录)")
            else:
                try:
                    month_dir.rmdir()
                except OSError:
                    failed += 1
                    print(f"  ❌ {display}  {label}  (空目录删除失败)")
                    continue
            cleaned += 1
            continue

        # 安全阀：每一个文件都必须确认已备份
        prefix = "/".join(month_dir.relative_to(backup_root).parts)
        unbacked = {t: manifest.unbacked_count(prefix, t) for t in require_targets}
        pending = {t: n for t, n in unbacked.items() if n > 0}

        if pending:
            detail = ", ".join(f"{t} 缺 {n} 个" for t, n in pending.items())
            print(f"  ⏭  {display}  {label}  (跳过: {detail})")
            kept += 1
            continue

        if args.dry_run:
            print(f"  🗑  {display}  {label}  (将移入废纸篓 — 已确认 {'+'.join(require_targets)} 备份)")
            cleaned += 1
            continue

        if delete_mode == "delete":
            try:
                shutil.rmtree(month_dir)
                ok = True
            except OSError:
                ok = False
        else:
            ok = move_to_trash(month_dir)

        if ok:
            print(f"  🗑  {display}  {label}  (已确认 {'+'.join(require_targets)} 备份)")
            cleaned += 1
        else:
            # 移动失败就留着 —— 绝不用硬删除兜底
            print(f"  ❌ {display}  {label}  (无法移入废纸篓，已保留)")
            failed += 1

    if not args.dry_run:
        removed = prune_empty_dirs(backup_root)
        if removed:
            print(f"  🧹 顺带清理了 {removed} 个空目录")

    print()
    print(f"清理 {cleaned} 个目录，保留 {kept} 个" + (f"，失败 {failed} 个" if failed else ""))
    print(f"[RESULT] cleaned={cleaned} kept={kept} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
