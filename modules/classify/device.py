#!/usr/bin/env python3.11
"""
device.py — 设备型号 → 归档文件夹名

把元数据里的原始机型字符串（如 "iPhone 14 Pro"）解析成归档用的文件夹名。
映射表在 config/device-models.json。

全项目只有这一处型号解析实现：Photos.app 导出的 photo_export.py 和
任意文件夹分类的 classify 模块都调用这里，避免两份规则各改各的。

为什么无型号时是 Unknown_Device 而不是 DEFAULT_DEVICE_MODEL：
    DEFAULT_DEVICE_MODEL 的语义是「Photos 库里没有相机信息的条目归到哪」，
    那批基本是机主手机存的网图。而散落在硬盘上的老素材来源不明，
    硬塞进主力机型会伪造出「这是机主拍的」的假象。两者语义不同，不能共用。
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, Optional

from modules.utils.config import PROJECT_ROOT, get_path

# 型号无法识别时的归属，也是 sanitize_model 的兜底返回值
UNKNOWN_DEVICE = "Unknown_Device"

_DEFAULT_MODELS_PATH = PROJECT_ROOT / "config" / "device-models.json"

# JSON 里以下划线开头的键是给人看的说明，不是映射条目
_META_KEY_PREFIX = "_"

# 可选的兜底名。设了它，读不出机型的文件就归到这个文件夹。
# 用武之地：一批老素材全是「元数据里没有设备信息」，但机主知道它们来自哪台机器。
# 不设则维持 Unknown_Device —— 默认行为与从前完全一致。
_UNKNOWN_FOLDER_KEY = "_unknown_folder_name"


def sanitize_model(model: str) -> str:
    """规范化设备型号：空格与分隔符→下划线，去重、去首尾，空则 Unknown_Device。

    "Some Camera Model" → "Some_Camera_Model"
    """
    sanitized = re.sub(r"[\s/\\,()]+", "_", str(model))
    sanitized = re.sub(r"_+", "_", sanitized)
    sanitized = sanitized.strip("_")
    return sanitized if sanitized else UNKNOWN_DEVICE


def models_path() -> Path:
    """映射表路径。可用 config 的 DEVICE_MODELS_PATH 覆盖。"""
    return get_path("DEVICE_MODELS_PATH", _DEFAULT_MODELS_PATH)


def load_device_models(path: Optional[Path] = None) -> Dict[str, dict]:
    """读取映射表原文（含下划线开头的说明键）。

    文件缺失返回空字典——这不算错误，只是没有映射，全部走规范化命名。
    文件损坏会告警但不中断：分类不该因为一个配置文件写坏就整个跑不动。
    """
    p = path or models_path()
    if not p or not Path(p).is_file():
        return {}
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, OSError) as e:
        print(f"⚠️  设备型号文件无法解析: {p} — {e}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def device_entries(path: Optional[Path] = None) -> Dict[str, dict]:
    """只要真正的映射条目，滤掉 _description / _instructions 这类说明键。"""
    return {
        k: v for k, v in load_device_models(path).items()
        if not k.startswith(_META_KEY_PREFIX) and isinstance(v, dict)
    }


def unknown_folder_name(mapping: Optional[Dict[str, dict]] = None) -> str:
    """读不出机型时归到哪个文件夹。默认 Unknown_Device。

    device-models.json 里可以用 "_unknown_folder_name" 改掉它。
    """
    table = mapping if mapping is not None else load_device_models()
    custom = table.get(_UNKNOWN_FOLDER_KEY)
    if isinstance(custom, str) and custom.strip():
        return custom.strip()
    return UNKNOWN_DEVICE


def resolve_folder_name(model: Optional[str], mapping: Optional[Dict[str, dict]] = None) -> str:
    """机型字符串 → 归档文件夹名。

    命中映射表则用表里的 folder_name；否则把原型号规范化后原样归档
    （不限于 Apple 设备，任何相机型号都能落进树里）。
    传入 None 或空串 → unknown_folder_name()，默认为 Unknown_Device。
    """
    if not model or not str(model).strip():
        return unknown_folder_name(mapping)

    raw = str(model).strip()
    table = mapping if mapping is not None else load_device_models()

    entry = table.get(raw)
    if isinstance(entry, dict):
        folder = entry.get("folder_name")
        if folder and str(folder).strip():
            return str(folder).strip()

    return sanitize_model(raw)


def spec_names(folder_name: str, mapping: Optional[Dict[str, dict]] = None) -> Optional[str]:
    """反查文件夹名对应的人/备注（owner 字段），用于界面展示与统计。"""
    table = mapping if mapping is not None else load_device_models()
    for info in table.values():
        if isinstance(info, dict) and info.get("folder_name") == folder_name:
            owner = info.get("owner")
            return str(owner) if owner else None
    return None
