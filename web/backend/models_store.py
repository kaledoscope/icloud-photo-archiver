#!/usr/bin/env python3.11
"""
models_store.py — 读写 config/device-models.json

扫描完发现一批文件归到了 Unknown_Device，界面上问「这是什么设备」，
在这里把答案写回映射表。下次扫描同一批素材就会自动归类。

写的是「机型 → 文件夹名」这张表，不碰 config.env：改 JSON 影响面
只有分类命名一处，改 .env 会牵动备份路径、邮箱等一大堆东西（v1 边界）。

写入安全：
  - 先写临时文件再原子替换，中途断电不会留下半个 JSON
  - 替换前留一份 .bak，写坏了手工回滚即可
  - folder_name 经过校验，不允许出现路径分隔符或 ..
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

# web/backend/models_store.py → 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from modules.classify import device as device_mod          # noqa: E402
from modules.utils.config import reindex                   # noqa: E402

# 文件夹名里不能出现的东西：路径分隔符、上跳、Windows 保留字符
_BAD_FOLDER_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


class ModelValidationError(ValueError):
    """用户填的内容不能作为文件夹名。"""


def models_file() -> Path:
    """映射表路径。每次现取，这样改了 DEVICE_MODELS_PATH 立刻生效。"""
    return Path(device_mod.models_path())


def read_raw() -> Dict:
    """读映射表原文（含下划线开头的说明键）。文件不存在返回空字典。"""
    return device_mod.load_device_models(models_file())


def list_entries() -> List[dict]:
    """给界面用的映射条目列表。顺带标出哪些是内置的 Apple 设备。"""
    out = []
    for exif_name, info in read_raw().items():
        if exif_name.startswith("_") or not isinstance(info, dict):
            continue
        folder = str(info.get("folder_name", "")).strip()
        out.append({
            "exif_name": exif_name,
            "folder_name": folder or device_mod.sanitize_model(exif_name),
            "owner": info.get("owner", ""),
            "color": info.get("color", ""),
        })
    out.sort(key=lambda e: e["folder_name"].lower())
    return out


def validate_folder_name(name: str) -> str:
    """文件夹名必须是单层、非空、不含路径分隔符的普通名字。"""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ModelValidationError("文件夹名不能为空")
    if cleaned in (".", ".."):
        raise ModelValidationError("文件夹名不能是 . 或 ..")
    if _BAD_FOLDER_CHARS.search(cleaned):
        raise ModelValidationError("文件夹名不能包含 / \\ : * ? \" < > | 等字符")
    if cleaned.startswith("."):
        raise ModelValidationError("文件夹名不能以点开头（会被当成隐藏目录）")
    if len(cleaned) > 100:
        raise ModelValidationError("文件夹名过长（上限 100 字符）")
    return cleaned


def upsert_entry(
    exif_name: str,
    folder_name: str,
    owner: str = "",
    color: str = "",
) -> dict:
    """新增或更新一条机型映射，写回 JSON。

    exif_name 是元数据里读出来的原始机型串（如 "iPhone 14 Pro"）。
    folder_name 留空则按规范化规则自动生成。
    """
    exif_name = (exif_name or "").strip()
    if not exif_name:
        raise ModelValidationError("机型名称不能为空")

    folder = validate_folder_name(
        folder_name.strip() if folder_name and folder_name.strip()
        else device_mod.sanitize_model(exif_name)
    )

    data = read_raw()
    entry = {"owner": (owner or "").strip(), "folder_name": folder}
    if color and color.strip():
        entry["color"] = color.strip()
    data[exif_name] = entry

    _write_atomic(data)
    # config 模块在 import 时缓存了配置值，写回后刷新，避免读到旧内容
    reindex()

    return {"exif_name": exif_name, **entry}


def _write_atomic(data: Dict) -> None:
    """先写 .tmp 再原子替换，中途失败不会破坏原文件。"""
    path = models_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    # 说明键保持在文件两头，真正的条目排在中间，读起来清楚
    head, body, tail = {}, {}, {}
    for k, v in data.items():
        if k.startswith("_") and not body:
            head[k] = v
        elif k.startswith("_"):
            tail[k] = v
        else:
            body[k] = v
    ordered = {**head, **body, **tail}

    text = json.dumps(ordered, ensure_ascii=False, indent=2) + "\n"

    # 留一份上一版，写坏了手工回滚即可
    if path.is_file():
        backup = path.with_suffix(path.suffix + ".bak")
        try:
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass    # 备份失败不该拦住写入

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)      # 原子替换：要么是旧内容，要么是新内容


def unknown_folder() -> str:
    """读不出机型的文件当前归到哪个文件夹（默认 Unknown_Device）。"""
    return device_mod.unknown_folder_name()


def set_unknown_folder(folder_name: str) -> dict:
    """给「读不出机型」的那批文件指定一个统一文件夹名。

    适用于：一批老素材元数据里根本没有设备信息，但机主知道它们来自哪台机器。
    传空字符串则恢复成默认的 Unknown_Device。
    """
    cleaned = (folder_name or "").strip()
    data = read_raw()

    if not cleaned:
        data.pop(device_mod._UNKNOWN_FOLDER_KEY, None)      # noqa: SLF001 — 同一个包内的常量
        _write_atomic(data)
        reindex()
        return {"folder_name": device_mod.UNKNOWN_DEVICE, "is_default": True}

    folder = validate_folder_name(cleaned)
    data[device_mod._UNKNOWN_FOLDER_KEY] = folder            # noqa: SLF001
    _write_atomic(data)
    reindex()
    return {"folder_name": folder, "is_default": False}


def device_options() -> List[dict]:
    """已存在的文件夹名集合，供界面下拉选择（不再新建时用）。"""
    seen: Dict[str, str] = {}
    for e in list_entries():
        seen.setdefault(e["folder_name"], e["owner"])
    return [{"folder_name": f, "owner": o} for f, o in sorted(seen.items())]
