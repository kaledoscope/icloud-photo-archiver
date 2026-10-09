#!/usr/bin/env python3.11
"""
scan.py — 扫描一个文件夹，产出分类计划（只读，不复制任何东西）

用法（从项目根调用）::

    python -m modules.classify.scan --input ~/Pictures/old --output ~/Classified
    python -m modules.classify.scan --input ~/Pictures/old --output /tmp/x --plan-out /tmp/plan.json
    python -m modules.classify.scan --input ~/Pictures/old --output /tmp/x --limit 50 --jobs 4

这一步只读源目录。要真正复制，把产出的计划交给 apply：

    python -m modules.classify.apply --plan /tmp/plan.json

输出（供编排方提取，格式不要改）::

    [PROGRESS] done=100 total=254
    [RESULT] planned=253 skipped=1 failed=0 total_bytes=123456
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from modules.classify import device as device_mod
from modules.classify import metadata as meta_mod
from modules.classify.planner import (
    MEDIA_PHOTOS,
    MEDIA_VIDEOS,
    SOURCE_PERSONAL,
    PathConflict,
    PlanBuilder,
    human_size,
    validate_dirs,
)

PLAN_VERSION = 1


def log(msg: str) -> None:
    print(msg, flush=True)


def tools_flag() -> str:
    """把外部工具的可用情况压成一个短标记，放进 [RESULT] 行。

    为什么值得占一个字段：ffprobe 缺失时所有视频会静默降级成
    Unknown_Device + 文件修改时间，扫描照样「成功」。编排方（网页后端、
    shell 脚本）靠这个字段才能发现「结果不可信」。
    """
    t = meta_mod.check_tools()
    missing = [k for k, ok in t.items() if not ok]
    return "ok" if not missing else "missing:" + ",".join(missing)


def warn_missing_tools() -> None:
    """工具缺失要说清楚后果，不能只报一句「找不到」。"""
    missing = [k for k, ok in meta_mod.check_tools().items() if not ok]
    if not missing:
        return
    log("")
    log("━" * 46)
    for name in missing:
        if name == "ffprobe":
            log("⚠️  ffprobe 不可用 —— 所有视频都读不出机型和拍摄时间")
            log("   后果：视频会被归到 Unknown_Device，年月用文件修改时间代替")
            log("   常见原因：ffprobe 由 Homebrew 装在 /opt/homebrew/bin，")
            log("   而 launchd 等环境看不到这个路径。")
            log("   解决：在 config/config.env 里写 FFPROBE_BIN=/opt/homebrew/bin/ffprobe")
        else:
            log(f"⚠️  {name} 不可用 —— 图片元数据读不出来")
    log("━" * 46)


def collect_files(root: Path) -> Tuple[List[Path], List[Tuple[Path, str]]]:
    """遍历源目录，返回 (待处理文件, 跳过的垃圾文件)。

    followlinks=False：不跟随符号链接，避免目录环和越出源树。
    遍历顺序排序，让同一份输入的两次扫描结果可比对。
    """
    keep: List[Path] = []
    junk: List[Tuple[Path, str]] = []

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if meta_mod.is_junk(p):
                junk.append((p, "system-junk"))
                continue
            keep.append(p)
    return keep, junk


def classify_one(path: Path) -> meta_mod.MediaMeta:
    """读一个文件的元数据。异常在这里收敛，不让单个坏文件中断整批。"""
    try:
        return meta_mod.read_metadata(path)
    except Exception:
        return meta_mod.MediaMeta(path=path, kind="unknown")


def build_plan(
    input_dir: Path,
    output_dir: Path,
    jobs: int = 8,
    limit: Optional[int] = None,
    progress_every: int = 100,
) -> PlanBuilder:
    """扫描 + 分类，返回计划（不落盘、不复制）。"""
    files, junk = collect_files(input_dir)
    if limit:
        files = files[:limit]

    builder = PlanBuilder(output_root=output_dir)
    for p, reason in junk:
        builder.skip(p, reason)

    log(f"扫描 {input_dir}")
    log(f"  待判定文件: {len(files)} 个（另有 {len(junk)} 个系统垃圾已跳过）")

    # 元数据读取是子进程开销（sips / ffprobe），用线程池并发
    metas: List[meta_mod.MediaMeta] = []
    total = len(files)
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(classify_one, p): p for p in files}
        for fut in as_completed(futures):
            metas.append(fut.result())
            done += 1
            if total >= 50 and (done % progress_every == 0 or done == total):
                log(f"[PROGRESS] done={done} total={total}")

    # 排序让结果稳定：同一份输入两次扫描得到同样的顺序
    metas.sort(key=lambda m: str(m.path))

    # 先处理媒体，记下 stem → 目标，供旁车文件跟随
    media_by_dir_stem = {}
    failed = 0

    for m in metas:
        p = m.path
        if meta_mod.is_sidecar(p):
            continue  # 第二趟处理
        if m.kind == "unknown":
            builder.skip(p, f"unsupported-ext:{p.suffix.lower() or '(none)'}")
            continue
        try:
            folder = device_mod.resolve_folder_name(m.model)
            media_type = MEDIA_VIDEOS if m.kind == "video" else MEDIA_PHOTOS
            entry = builder.add_media(
                src=p,
                device=folder,
                model=m.model,
                taken_at=m.taken_at,
                date_source=m.date_source,
                media_type=media_type,
                source=SOURCE_PERSONAL,
            )
            # Live Photo 会产生同名词干的照片 + 视频（IMG_5244.JPG + IMG_5244.MOV）。
            # 旁车 .aae 存的是【图像】的修图参数，必须跟随照片；
            # 让视频覆盖掉照片会让 AAE 飘到 videos/ 里去，等于丢失编辑信息。
            key = (str(p.parent), p.stem)
            existing = media_by_dir_stem.get(key)
            if existing is None or (existing.kind == "video" and entry.kind == "photo"):
                media_by_dir_stem[key] = entry
        except Exception as e:
            failed += 1
            builder.skip(p, f"error:{e}")

    # 旁车文件（.aae）：跟着同名主文件走，主文件不在计划里就一并跳过
    for m in metas:
        p = m.path
        if not meta_mod.is_sidecar(p):
            continue
        main = media_by_dir_stem.get((str(p.parent), p.stem))
        if main is None:
            builder.skip(p, "sidecar-without-media")
            continue
        try:
            builder.add_sidecar(src=p, beside=main)
        except Exception as e:
            failed += 1
            builder.skip(p, f"error:{e}")

    builder.failed = failed
    return builder


def plan_to_json(builder: PlanBuilder, input_dir: Path, output_dir: Path) -> dict:
    return {
        "version": PLAN_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "entries": [e.to_json() for e in builder.entries],
        "skipped": builder.skipped,
        "summary": builder.summary(),
        # 工具版本信息，便于以后排查「这份计划是谁生成的」
        "tool": "modules.classify.scan",
    }


def render_report(builder: PlanBuilder, input_dir: Path, output_dir: Path) -> None:
    s = builder.summary()
    log("")
    log("━━━ 分类计划 ━━━")
    log(f"源目录:   {input_dir}")
    log(f"输出目录: {output_dir}")
    log("")
    log(f"将复制 {s['files']} 个文件，共 {human_size(s['total_bytes'])}")
    log(f"跳过   {s['skipped']} 个")
    if s["skipped_by_reason"]:
        for reason, cnt in sorted(s["skipped_by_reason"].items(), key=lambda x: -x[1]):
            log(f"   {reason}: {cnt}")
    log("")
    log("按设备型号:")
    for dev, counts in sorted(s["by_device"].items(), key=lambda x: -(x[1]["photos"] + x[1]["videos"])):
        bits = []
        if counts["photos"]:
            bits.append(f"照片 {counts['photos']}")
        if counts["videos"]:
            bits.append(f"视频 {counts['videos']}")
        if counts["sidecars"]:
            bits.append(f"旁车 {counts['sidecars']}")
        log(f"   {dev}: {', '.join(bits)}")

    unknown_folder = device_mod.unknown_folder_name()
    if unknown_folder in s["by_device"]:
        counts = s["by_device"][unknown_folder]
        log("")
        log(f"⚠️  有 {counts['photos'] + counts['videos']} 个文件读不出机型，"
            f"已归入 {unknown_folder}/")
        log("   可以在 config/device-models.json 里补映射，或使用网页界面命名")

    # 读得出机型、但映射表里没有的：文件夹名是按型号拼出来的，往往需要人工确认
    mapped = {e.get("folder_name") for e in device_mod.device_entries().values()}
    unmapped = [d for d in s["by_device"] if d not in mapped and d != unknown_folder]
    if unmapped:
        log("")
        log("ℹ️  以下设备的文件夹名是按型号自动拼的，可以到 config/device-models.json 里改成更好认的名字:")
        for name in sorted(unmapped):
            log(f"   {name}")

    if s["by_date_source"].get("mtime"):
        log(f"ℹ️  {s['by_date_source']['mtime']} 个文件没有拍摄时间，用了文件修改时间")
    log("━━━━━━━━━━━━━━━")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="扫描文件夹，按五级规则生成分类计划（只读，不复制）"
    )
    ap.add_argument("--input", required=True, help="要分类的源文件夹")
    ap.add_argument("--output", required=True, help="分类结果的输出根目录")
    ap.add_argument("--plan-out", help="把计划写成 JSON 文件（供 apply 使用）")
    ap.add_argument("--jobs", type=int, default=8, help="并发读取元数据的线程数（默认 8）")
    ap.add_argument("--limit", type=int, help="只处理前 N 个文件（调试用）")
    args = ap.parse_args()

    input_dir = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()

    if not input_dir.is_dir():
        log(f"❌ 源目录不存在或不是目录: {input_dir}")
        log("[RESULT] planned=0 skipped=0 failed=1 total_bytes=0")
        return 1

    try:
        validate_dirs(input_dir, output_dir)
    except PathConflict as e:
        log(f"❌ {e}")
        log("[RESULT] planned=0 skipped=0 failed=1 total_bytes=0")
        return 1

    warn_missing_tools()

    builder = build_plan(
        input_dir, output_dir, jobs=args.jobs, limit=args.limit
    )
    render_report(builder, input_dir, output_dir)

    if args.plan_out:
        out = Path(args.plan_out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(plan_to_json(builder, input_dir, output_dir), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(f"计划已写入: {out}")

    s = builder.summary()
    log(f"[RESULT] planned={s['files']} skipped={s['skipped']} "
        f"failed={builder.failed} total_bytes={s['total_bytes']} "
        f"tools={tools_flag()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
