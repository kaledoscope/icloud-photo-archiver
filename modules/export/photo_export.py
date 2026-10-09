#!/usr/bin/env python3.11
"""
photo_export.py — 从 Photos.app 导出照片视频，按 设备型号/年/月/来源/类型 五级分类

第一性原理：家庭场景中机型=人，最外层按「谁拍的」组织，每个人打开备份直接看到自己的文件夹。

核心逻辑：
1. 读取上次导出时间戳 → 决定全量/增量模式
2. 从 Photos 库查询项目
3. 按五级分类：设备型号(谁) → 年/月(何时) → 来源(场景) → 照片/视频(类型)
4. 计算 SHA256 哈希，写入 manifest 清单
5. 更新状态文件

用法（从项目根调用）::

    python -m modules.export.photo_export              # 增量导出
    python -m modules.export.photo_export --dry-run    # 预览不写文件
    python -m modules.export.photo_export --full       # 全量导出（忽略时间戳）
    python -m modules.export.photo_export --verbose    # 详细输出

临时换备份根目录（环境变量优先于配置文件）::

    BACKUP_ROOT=/tmp/x python -m modules.export.photo_export --dry-run
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Tuple

try:
    import osxphotos
    from osxphotos import PhotosDB, QueryOptions
except ImportError:
    print("❌ osxphotos 未安装。请执行: pip3 install osxphotos")
    sys.exit(2)


# ===== 配置（统一来自 config/，不要在此硬编码环境相关值）=====
from modules.utils.config import get, get_int, require_path
from modules.utils.files import compute_sha256, human_size
from modules.utils.manifest import Manifest, empty_flags

# 分类规则来自共用引擎：Photos 导出的照片与硬盘上的老素材走同一套规则，
# 避免「五级路径模板」和「型号映射」在两处各存一份、各改各的。
from modules.classify import device as device_mod
from modules.classify.planner import build_relative_path, dedupe_relative

BACKUP_ROOT = require_path("BACKUP_ROOT")

STATE_DIR = BACKUP_ROOT / ".state"
LOGS_DIR = BACKUP_ROOT / ".logs"
STATE_FILE = STATE_DIR / "last_export.json"
LOG_FILE = LOGS_DIR / "export.log"

# 家庭共享相簿名称列表（逗号分隔）
FAMILY_ALBUMS = [
    a.strip() for a in get("FAMILY_ALBUMS", "Family,家庭,家人共享,家庭聚会,共享相簿").split(",")
    if a.strip()
]

# 无 EXIF 设备型号时的默认归属（设为空则仍用 "Unknown_Device"）
DEFAULT_DEVICE_MODEL = get("DEFAULT_DEVICE_MODEL", "")

# 导出窗口（月）
EXPORT_WINDOW_MONTHS = get_int("EXPORT_WINDOW_MONTHS", 3)


def log(msg: str):
    """写日志"""
    timestamp = datetime.now().isoformat()
    line = f"[{timestamp}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_newest_backup_mtime() -> Optional[datetime]:
    """扫描本地中转文件夹，找到最新文件的修改时间。

    文件系统本身就是最可靠的状态记录——不依赖外部状态文件的 timestamp。
    跳过 .state/ .logs/ .Trash 等元数据目录和隐藏文件。

    返回: 最新文件的 mtime（带时区），若目录为空则返回 None
    """
    newest = None
    try:
        for root, dirs, files in os.walk(BACKUP_ROOT):
            # 跳过元数据目录
            dirs[:] = [d for d in dirs if d not in ('.state', '.logs', '.Trash')]
            for fname in files:
                if fname.startswith('.') or fname == '.gitkeep':
                    continue
                try:
                    mtime = os.path.getmtime(os.path.join(root, fname))
                    if newest is None or mtime > newest:
                        newest = mtime
                except OSError:
                    pass
    except Exception:
        pass

    if newest is not None:
        return datetime.fromtimestamp(newest).astimezone()
    return None


def load_state() -> Optional[datetime]:
    """加载上次导出时间戳（仅用于日志参考，不作为增量判断依据）"""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            ts = data.get("last_export_ts")
            if ts:
                return datetime.fromisoformat(ts)
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
    return None


def save_state(export_count: int, duration_sec: float):
    """保存本次导出信息（仅用于日志参考和状态追踪）"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "last_export_ts": datetime.now().astimezone().isoformat(),
        "total_exported": export_count,
        "last_run_duration_sec": round(duration_sec, 1)
    }, indent=2, ensure_ascii=False))


def classify_source(photo) -> str:
    """
    判定照片来源: personal / family-shared

    策略：检查照片所属的相簿名称，匹配 FAMILY_ALBUMS 中的关键词
    """
    try:
        album_names = [a.title for a in photo.album_info]
        for album_name in album_names:
            for fa in FAMILY_ALBUMS:
                if fa.lower() in album_name.lower():
                    return "family-shared"
    except Exception:
        pass
    return "personal"


def classify_device(photo) -> str:
    """
    从 EXIF 提取设备型号，返回规范化的文件夹名称

    映射与规范化规则统一由 modules/classify/device.py 提供，
    与「任意文件夹分类」共用同一套，不在此另存一份。

    与那个模块唯一的语义差别：Photos 库里没有相机信息的条目
    （基本是机主手机存的网图）按 DEFAULT_DEVICE_MODEL 归入主力机型；
    硬盘上来源不明的老素材则归 Unknown_Device，不冒充某人拍的。
    """
    try:
        exif = photo.exif_info
        camera_model = None

        # 尝试从 EXIF 获取相机型号
        if exif:
            if hasattr(exif, 'camera_model') and exif.camera_model:
                camera_model = str(exif.camera_model).strip()
            elif hasattr(exif, 'model') and exif.model:
                camera_model = str(exif.model).strip()

        if not camera_model:
            # 使用配置的默认设备（匹配当前设备的非相机来源文件）
            camera_model = DEFAULT_DEVICE_MODEL

        return device_mod.resolve_folder_name(camera_model)

    except Exception as e:
        log(f"⚠️  获取设备型号失败: {e}")
        return DEFAULT_DEVICE_MODEL if DEFAULT_DEVICE_MODEL else device_mod.unknown_folder_name()


def classify_media_type(photo) -> str:
    """区分照片/视频"""
    if photo.ismovie:
        return "videos"
    return "photos"


def classify_asset(photo) -> Tuple[str, str, str]:
    """
    对单个媒体资产进行完整分类

    返回: (source, device_model, media_type)
    - source: "personal" | "family-shared"
    - device_model: 规范化后的机型名，如 "iPhone_15_Pro" | "Pixel_8" | ...
    - media_type: "photos" | "videos"
    """
    source = classify_source(photo)
    device = classify_device(photo)
    media_type = classify_media_type(photo)
    return source, device, media_type


def get_export_path(photo, source: str, device_model: str, media_type: str) -> Path:
    """
    确定导出目标路径

    格式: BACKUP_ROOT/{device_model}/YYYY/MM/{source}/{media_type}/{filename}

    层次设计（从第一性原理）：
    - 第1层: 设备型号 = 谁拍的（家庭场景中机型=人，最自然的浏览入口）
    - 第2层: 年份
    - 第3层: 月份
    - 第4层: 来源 = personal / family-shared
    - 第5层: 媒体类型 = photos / videos

    路径模板与重名规则都由 modules/classify/planner.py 提供——全项目一份实现，
    「任意文件夹分类」走的是同一套，不会两处规则各改各的。
    """
    # 使用拍摄日期分类——目录结构反映"何时拍摄"，而非"何时导入"
    # 若拍摄日期不可用则回退到入库日期，再不行用当前时间
    dt = photo.date or photo.date_added
    if not dt:
        dt = datetime.now()

    rel = build_relative_path(
        device=device_model,
        taken_at=dt,
        source=source,
        media_type=media_type,
        filename=photo.original_filename,
    )
    # 同名不覆盖：追加拍摄时间戳，仍冲突再加递增序号
    rel = dedupe_relative(rel, dt, lambda r: (BACKUP_ROOT / r).exists())
    return BACKUP_ROOT / rel


def export_photos(full_mode: bool = False, dry_run: bool = False, verbose: bool = False) -> int:
    """
    主导出函数

    参数:
    - full_mode: True=全量导出（忽略上次时间戳）
    - dry_run: True=仅预览不写文件

    返回: 导出的文件数量；-1 表示 Photos 库连不上（本次根本没跑成）
    """
    start_time = datetime.now()
    log("===== 开始导出 =====")

    # 1. 连接 Photos 库
    try:
        photosdb = PhotosDB()
        all_photos = list(photosdb.photos())
        log(f"Photos 库包含 {len(all_photos)} 个项目")
    except Exception as e:
        log(f"❌ 无法连接 Photos 库: {e}")
        # 必须发 RESULT：否则编排脚本读不到 failed，会把"根本没跑"报成"成功 0 文件"
        log("[RESULT] exported=0 skipped=0 failed=1 size_bytes=0")
        return -1

    # 2. 确定导出范围（以本地中转文件夹的磁盘内容为 ground truth）
    if full_mode:
        new_photos = all_photos
        log(f"全量模式（强制）: {len(new_photos)} 个项目")
    else:
        newest_backup = get_newest_backup_mtime()
        if newest_backup:
            # 中转文件夹有内容 → 只导出比最新文件更新的照片
            new_photos = [p for p in all_photos
                          if p.date_added and p.date_added > newest_backup]
            log(f"增量模式：中转文件夹最新文件 {newest_backup.isoformat()}")
            log(f"  以此为基准，Photos.app 中 date_added 在此之后的新增项目: {len(new_photos)}")
        else:
            # 中转文件夹为空 → 首次运行，全量
            new_photos = all_photos
            log(f"全量模式（中转文件夹为空/首次运行）: {len(new_photos)} 个项目")

    # 3. 过滤窗口期（full_mode 下跳过窗口限制，全量导出）
    if full_mode:
        photos_to_export = [
            p for p in new_photos
            if (p.date or p.date_added)
        ]
        log(f"全量模式：跳过窗口限制，共 {len(photos_to_export)} 个项目")
    else:
        window_start = datetime.now().astimezone() - timedelta(days=EXPORT_WINDOW_MONTHS * 30)
        photos_to_export = [
            p for p in new_photos
            if (p.date or p.date_added) and (p.date or p.date_added) >= window_start
        ]
        log(f"窗口期 ({EXPORT_WINDOW_MONTHS} 个月) 内: {len(photos_to_export)} 个项目")

    if not photos_to_export:
        log("没有需要导出的项目")
        log("[RESULT] exported=0 skipped=0 failed=0 size_bytes=0")
        return 0

    # 4. 加载已有清单（去重用）
    manifest = Manifest()
    exported_count = 0
    skipped_count = 0
    error_count = 0
    total_size = 0

    # 5. 按设备型号统计
    device_stats: Dict[str, int] = {}

    # 6. 逐文件导出
    for i, photo in enumerate(photos_to_export):
        try:
            source, device_model, media_type = classify_asset(photo)
            target_path = get_export_path(photo, source, device_model, media_type)

            # 统计设备
            device_stats[device_model] = device_stats.get(device_model, 0) + 1

            # 去重：基于 asset UUID
            asset_uuid = str(photo.uuid) if photo.uuid else ""

            # 检查是否已在清单中（uuid 索引查，不做线性扫描）
            already_exported = bool(asset_uuid) and manifest.find_by_uuid(asset_uuid) is not None
            if already_exported and not full_mode:
                skipped_count += 1
                if verbose:
                    log(f"  跳过(已导出): {photo.original_filename}")
                continue

            if dry_run:
                log(f"[DRY RUN] {photo.original_filename} → {target_path}")
                exported_count += 1
                continue

            # 实际导出
            target_path.parent.mkdir(parents=True, exist_ok=True)

            exported = photo.export(
                str(target_path.parent),
                filename=target_path.name,
                edited=False,              # 导出原片（非编辑后版本）
                live_photo=True,           # Live Photo 成对导出
                overwrite=False,
            )

            if exported and len(exported) > 0:
                actual_path = Path(exported[0])
                file_size = actual_path.stat().st_size
                file_hash = compute_sha256(actual_path)
                total_size += file_size

                # 记录到清单。备份目标的标记字段由 config 的 BACKUP_TARGETS 决定，
                # 新增一个备份目标不需要改这里。
                relative_key = str(target_path.relative_to(BACKUP_ROOT))
                manifest.files[relative_key] = {
                    "sha256": file_hash,
                    "exported_at": datetime.now().isoformat(),
                    **empty_flags(),
                    "original_asset_uuid": asset_uuid,
                    "source": source,
                    "device_model": device_model,
                    "media_type": media_type,
                    "size_bytes": file_size,
                    "original_filename": photo.original_filename,
                }

                exported_count += 1

                # 进度汇报
                if verbose:
                    log(f"  ✅ {photo.original_filename} → {target_path} ({human_size(file_size)})")
                elif exported_count % 100 == 0:
                    log(f"已导出 {exported_count} 个文件 ({human_size(total_size)})...")
            else:
                log(f"  ⚠️  导出返回空: {photo.original_filename}")

        except Exception as e:
            error_count += 1
            log(f"❌ 导出失败: {photo.original_filename} — {e}")
            if verbose:
                import traceback
                traceback.print_exc()

    # 7. 保存状态
    # 增量基准已改为扫描中转文件夹磁盘内容（get_newest_backup_mtime），
    # last_export_ts 仅作日志参考，不再参与导出判断。
    # 只在有实际导出时才更新，保持日志信息有意义。
    # dry_run 全程不落盘 —— 预演就该是只读的。
    duration = (datetime.now() - start_time).total_seconds()
    if dry_run:
        log("预览模式：不写清单、不更状态")
    else:
        manifest.save()
        if exported_count > 0:
            save_state(exported_count, duration)
        else:
            log("无新增导出，跳过更新时间戳（防止死亡区间）")

    # 8. 汇总报告
    log("===== 导出报告 =====")
    log(f"新增导出: {exported_count} 个文件")
    log(f"跳过(已存在): {skipped_count} 个文件")
    log(f"失败: {error_count} 个文件")
    log(f"总大小: {human_size(total_size)}")
    log(f"耗时: {duration:.1f}s")
    log("")
    log("按设备型号统计:")
    for device, count in sorted(device_stats.items(), key=lambda x: -x[1]):
        log(f"  {device}: {count} 个文件")
    log("===== 导出完成 =====")

    # 机器可读结果行，供 shell 提取，不要改格式
    log(f"[RESULT] exported={exported_count} skipped={skipped_count} "
        f"failed={error_count} size_bytes={total_size}")

    return exported_count


def main():
    parser = argparse.ArgumentParser(
        description="从 Photos.app 导出照片视频，按 年/月/来源/设备型号/类型 分类"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="预览模式：仅显示将导出的文件，不实际写入"
    )
    parser.add_argument(
        "--full", action="store_true",
        help="全量导出：忽略上次导出时间戳，导出所有窗口期内的照片"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="详细输出每个文件的导出结果"
    )
    args = parser.parse_args()

    # 想换备份根目录不必加参数：环境变量优先级高于配置文件，
    #   BACKUP_ROOT=/tmp/x python -m modules.export.photo_export --dry-run

    exported = export_photos(
        full_mode=args.full,
        dry_run=args.dry_run,
        verbose=args.verbose
    )
    # 连不上 Photos 库要以非零退出，让编排脚本和手动运行都能立刻看出没跑成
    if exported < 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
