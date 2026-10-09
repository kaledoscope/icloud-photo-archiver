#!/usr/bin/env python3.11
"""
files.py — 文件相关的共用小工具

放在 utils/ 这个共享地基里，供 export、classify 等模块共用。
之前 compute_sha256 和 format_size 长在 photo_export.py 里，
分类模块要用就得复制一份——那正是「同一件事两份实现」的开端。
"""

import hashlib
from pathlib import Path

# 读哈希时的块大小
_CHUNK = 65536


def compute_sha256(filepath: Path) -> str:
    """计算文件的 SHA256（流式读取，大视频也不会吃满内存）。只读，不改文件。"""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def human_size(n: int) -> str:
    """1536 → '1.5 KB'。用于所有面向人的体积显示。"""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"
