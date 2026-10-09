#!/usr/bin/env python3.11
"""
metadata.py — 从文件本身读出「谁拍的、何时拍的、是照片还是视频」

不依赖 Photos 库，只看文件。全程只读，不碰源文件。

为什么用系统自带命令而不是 Python 库：
    实测 Pillow / exifread / piexif / hachoir 全都没装，而 macOS 自带
    sips（图片，原生支持 HEIC）与 ffprobe（视频）已经能给出机型和时间。
    为一个读元数据的需求引入新依赖不划算，也少一层版本折腾。

    sips    图片 → model / creation
    ffprobe 视频 → com.apple.quicktime.model / .creationdate

关于视频时间为什么要优先 creationdate：
    creation_time     是 UTC（...Z）
    creationdate      带原始时区（...+0900）
    晚上 7 点在东京拍的片子，用 UTC 会变成当天 10 点——跨午夜时直接错一天，
    进而落进错误的年月目录。所以带时区的那个优先。
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from modules.utils.config import get

# 图片扩展名。sips 原生支持 HEIC/HEIF，iPhone 直出不用转格式。
PHOTO_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tif", ".tiff",
    ".bmp", ".webp", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2",
})

# 视频扩展名
VIDEO_EXTS = frozenset({
    ".mov", ".mp4", ".m4v", ".avi", ".mkv", ".3gp", ".3gpp",
    ".mts", ".m2ts", ".wmv", ".flv", ".webm", ".mpg", ".mpeg",
})

# Apple 的修图旁车文件：本身不是媒体，但要跟着主文件走
SIDECAR_EXTS = frozenset({".aae"})

# 系统垃圾，永远跳过
JUNK_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})

SUBPROCESS_TIMEOUT = 20

# 外部工具只按名字调用是不够的：launchd 拉起的进程 PATH 只有
# /usr/bin:/bin:/usr/sbin:/sbin，而 Homebrew 装在 /opt/homebrew/bin。
# 后果是 sips（在 /usr/bin）正常、ffprobe 找不到——所有视频会静默降级成
# Unknown_Device + 文件修改时间，任务还报成功。所以两个工具都显式定位：
# 先看 FFPROBE_BIN / SIPS_BIN 配置，再查 PATH，最后查常见安装位置。
_FALLBACK_DIRS = (
    "/opt/homebrew/bin",     # Apple Silicon Homebrew
    "/usr/local/bin",        # Intel Homebrew
    "/opt/local/bin",        # MacPorts
    "/usr/bin",
    "/bin",
)


def _locate(name: str) -> Optional[str]:
    """找到可执行文件的绝对路径。找不到返回 None（不抛异常）。"""
    override = get(f"{name.upper()}_BIN")
    if override:
        p = Path(str(override)).expanduser()
        return str(p) if p.is_file() else None

    found = shutil.which(name)
    if found:
        return found

    for d in _FALLBACK_DIRS:
        candidate = Path(d) / name
        if candidate.is_file():
            return str(candidate)
    return None


# 启动时解析一次。工具缺失不该让模块 import 就崩——那是「整批跑不动」，
# 比「读不出元数据」更糟；缺失由 check_tools() 显式报出来。
FFPROBE_BIN = _locate("ffprobe")
SIPS_BIN = _locate("sips")


def check_tools() -> Dict[str, bool]:
    """外部工具是否就位。扫描前调用，缺了要大声报，不能静默降级。"""
    return {
        "sips": SIPS_BIN is not None,
        "ffprobe": FFPROBE_BIN is not None,
    }


# sips 输出形如 "  model: iPhone 14 Pro"
_SIPS_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")

# EXIF 风格时间 "2025:02:08 19:19:57"（年月日之间是冒号）
_EXIF_DT_RE = re.compile(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")

# ISO 8601 带时区，"2025-02-08T19:19:57+0900" 或 "...Z"
_ISO_OFFSET_RE = re.compile(r"([+-]\d{2})(\d{2})$")


@dataclass
class MediaMeta:
    """一个媒体文件的判定结果。

    taken_at 带时区；无法判定时为 None（调用方回退到文件 mtime）。
    kind 为 "photo" | "video"。
    """
    path: Path
    kind: str
    model: Optional[str] = None
    taken_at: Optional[datetime] = None
    date_source: str = "unknown"   # exif | mtime | unknown

    @property
    def has_camera_model(self) -> bool:
        return bool(self.model and self.model.strip())


def is_photo(path: Path) -> bool:
    return path.suffix.lower() in PHOTO_EXTS


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def is_sidecar(path: Path) -> bool:
    return path.suffix.lower() in SIDECAR_EXTS


def is_junk(path: Path) -> bool:
    """系统垃圾与 macOS 资源分叉文件（._开头）。"""
    name = path.name
    return name in JUNK_NAMES or name.startswith("._")


def _parse_exif_datetime(raw: str) -> Optional[datetime]:
    """"2025:02:08 19:19:57" → 带本地时区的 datetime。"""
    m = _EXIF_DT_RE.match(raw.strip())
    if not m:
        return None
    try:
        y, mo, d, h, mi, s = (int(g) for g in m.groups())
        return datetime(y, mo, d, h, mi, s).astimezone()
    except ValueError:
        return None


def _parse_iso_datetime(raw: str) -> Optional[datetime]:
    """解析 ffprobe 的时间戳，保留其自带时区（无时区则按 UTC）。"""
    txt = raw.strip()
    if not txt or txt.startswith("0000"):
        return None
    normalized = _ISO_OFFSET_RE.sub(r"\1:\2", txt)
    try:
        dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _run(cmd: list) -> Optional[str]:
    """跑一条只读命令，失败返回 None（不抛异常——单个文件读不出不该中断整批）。

    注意：这里吞掉异常是刻意的（个别文件损坏不该毁掉整批），
    所以「工具根本不存在」必须在扫描开始前由 check_tools() 拦下并报出来，
    否则就会变成静默降级。
    """
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _read_photo(path: Path) -> tuple:
    """sips 读图片 → (model, taken_at)"""
    if not SIPS_BIN:
        return None, None
    out = _run([SIPS_BIN, "-g", "model", "-g", "creation", str(path)])
    if not out:
        return None, None

    model = None
    taken = None
    for line in out.splitlines():
        m = _SIPS_LINE_RE.match(line)
        if not m:
            continue
        key, val = m.group(1).lower(), m.group(2).strip()
        if key == "model" and val:
            model = val
        elif key == "creation" and val:
            taken = _parse_exif_datetime(val)
    return model, taken


def _read_video(path: Path) -> tuple:
    """ffprobe 读视频 → (model, taken_at)"""
    if not FFPROBE_BIN:
        return None, None
    out = _run([
        FFPROBE_BIN, "-v", "quiet", "-print_format", "json",
        "-show_entries", "format_tags", str(path),
    ])
    if not out:
        return None, None
    try:
        tags = json.loads(out).get("format", {}).get("tags", {})
    except (json.JSONDecodeError, AttributeError):
        return None, None

    lowered = {k.lower(): v for k, v in tags.items()}
    model = lowered.get("com.apple.quicktime.model") or lowered.get("model")

    # 带时区的优先；退回 UTC 的 creation_time
    taken = None
    for key in ("com.apple.quicktime.creationdate", "creation_time"):
        raw = lowered.get(key)
        if raw:
            taken = _parse_iso_datetime(raw)
            if taken is not None:
                break
    return (model.strip() if model else None), taken


def read_metadata(path: Path) -> MediaMeta:
    """读一个媒体文件的元数据。只读，绝不修改源文件。

    读不出机型是正常的（老视频、微信保存的图、截图都没有），
    此时 model 为 None，由调用方归到 Unknown_Device。
    """
    p = Path(path)
    if is_video(p):
        kind = "video"
        model, taken = _read_video(p)
    elif is_photo(p):
        kind = "photo"
        model, taken = _read_photo(p)
    else:
        return MediaMeta(path=p, kind="unknown")

    if taken is not None:
        return MediaMeta(path=p, kind=kind, model=model, taken_at=taken, date_source="exif")

    # 没有拍摄时间就用文件 mtime 兜底——聊胜于无，且在报告里标明来源
    try:
        mtime = datetime.fromtimestamp(p.stat().st_mtime).astimezone()
    except OSError:
        return MediaMeta(path=p, kind=kind, model=model)
    return MediaMeta(path=p, kind=kind, model=model, taken_at=mtime, date_source="mtime")
