#!/usr/bin/env python3.11
"""
apply.py — 按分类计划复制文件（源文件只读，绝不删除、绝不覆盖）

用法（从项目根调用）::

    python -m modules.classify.apply --plan /tmp/plan.json
    python -m modules.classify.apply --plan /tmp/plan.json --dry-run

安全约束（每一条都是硬保证，不是约定）:
    1. 源文件只以只读方式参与，全流程不存在对源路径的写/删/移调用
    2. 目标路径必须落在计划声明的 output_dir 之内 —— 计划文件被改坏也写不出去
    3. 目标已存在就跳过，绝不覆盖
    4. 没有删除逻辑，这个模块里没有 rm/unlink/rmtree/trash

输出（供编排方提取，格式不要改）::

    [PROGRESS] done=100 total=254
    [RESULT] copied=253 skipped=1 failed=0 bytes=123456
"""

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from modules.classify.planner import (
    PathConflict,
    PlanEntry,
    human_size,
    validate_dirs,
)

PROGRESS_EVERY = 50


def log(msg: str) -> None:
    print(msg, flush=True)


def load_plan(plan_path: Path) -> dict:
    data = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "entries" not in data:
        raise ValueError("计划文件格式不正确")
    return data


def check_space(output_dir: Path, need_bytes: int) -> bool:
    """输出磁盘空间是否够。不够就别开始拷，拷一半失败更难收拾。"""
    probe = output_dir
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return True  # 问不出来就不拦，交给复制过程自己报错
    if need_bytes > free:
        log(f"❌ 输出磁盘空间不足：需要 {human_size(need_bytes)}，可用 {human_size(free)}")
        return False
    return True


def is_within(child: Path, parent: Path) -> bool:
    """child 是否在 parent 之内（含相等）。用于挡住越界写入。"""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def apply_plan(plan: dict, dry_run: bool = False) -> Tuple[int, int, int, int]:
    """执行复制。返回 (copied, skipped, failed, bytes)。"""
    output_root = Path(plan["output_dir"]).expanduser().resolve()
    entries = [PlanEntry.from_json(e) for e in plan["entries"]]

    total = len(entries)
    copied = skipped = failed = 0
    copied_bytes = 0

    if not dry_run:
        output_root.mkdir(parents=True, exist_ok=True)

    for i, entry in enumerate(entries, 1):
        src = Path(entry.src)
        dst = output_root / entry.rel

        # 硬保证：目标必须在输出目录内。计划文件被改坏也写不出去。
        if not is_within(dst, output_root):
            failed += 1
            log(f"❌ 目标越界，已拒绝: {dst}")
            continue

        if not src.is_file():
            failed += 1
            log(f"❌ 源文件不存在: {src}")
            continue

        if dst.exists():
            skipped += 1
            continue

        if dry_run:
            copied += 1
            copied_bytes += entry.size_bytes
            continue

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            # copy2 保留 mtime——备份的保真度靠它
            shutil.copy2(src, dst)
            copied += 1
            copied_bytes += entry.size_bytes
        except (OSError, shutil.Error) as e:
            failed += 1
            log(f"❌ 复制失败: {src.name} — {e}")

        if total >= 100 and (i % PROGRESS_EVERY == 0 or i == total):
            log(f"[PROGRESS] done={i} total={total}")

    return copied, skipped, failed, copied_bytes


def main() -> int:
    ap = argparse.ArgumentParser(description="按计划复制文件（源文件只读）")
    ap.add_argument("--plan", required=True, help="scan 产出的计划 JSON")
    ap.add_argument("--dry-run", action="store_true", help="只统计不复制")
    args = ap.parse_args()

    plan_path = Path(args.plan).expanduser()
    if not plan_path.is_file():
        log(f"❌ 计划文件不存在: {plan_path}")
        log("[RESULT] copied=0 skipped=0 failed=1 bytes=0")
        return 1

    try:
        plan = load_plan(plan_path)
    except (json.JSONDecodeError, ValueError, OSError) as e:
        log(f"❌ 无法读取计划: {e}")
        log("[RESULT] copied=0 skipped=0 failed=1 bytes=0")
        return 1

    input_dir = Path(plan["input_dir"]).expanduser().resolve()
    output_dir = Path(plan["output_dir"]).expanduser().resolve()

    # 复查目录关系：计划生成后到执行前，路径可能已被改动
    try:
        validate_dirs(input_dir, output_dir)
    except PathConflict as e:
        log(f"❌ {e}")
        log("[RESULT] copied=0 skipped=0 failed=1 bytes=0")
        return 1

    total_bytes = int(plan.get("summary", {}).get("total_bytes", 0))
    if not args.dry_run and not check_space(output_dir, total_bytes):
        log("[RESULT] copied=0 skipped=0 failed=1 bytes=0")
        return 1

    log(f"计划: {plan_path}")
    log(f"  源   {input_dir}")
    log(f"  目标 {output_dir}")
    log(f"  {len(plan['entries'])} 个文件，{human_size(total_bytes)}")
    if args.dry_run:
        log("  [预演模式] 不写任何文件")
    log("")

    copied, skipped, failed, nbytes = apply_plan(plan, dry_run=args.dry_run)

    log("")
    log("━━━ 复制结果 ━━━")
    log(f"已复制: {copied} 个，{human_size(nbytes)}")
    log(f"跳过(目标已存在): {skipped} 个")
    log(f"失败: {failed} 个")
    log("源目录未被改动。")
    log("━━━━━━━━━━━━━━")

    log(f"[RESULT] copied={copied} skipped={skipped} failed={failed} bytes={nbytes}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
