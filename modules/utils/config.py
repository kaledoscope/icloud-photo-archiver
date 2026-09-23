#!/usr/bin/env python3.11
"""
config.py — 统一配置读取（Python 侧）

与 config/load.sh 同源同规则，供所有 .py 模块使用。

设计原则：代码只负责功能，凡"随环境变化"的值一律放在 config/ 下。

读取内容:
    config/config.env     公用配置（路径、阈值、开关）
    config/secrets.env    个人 / 保密配置（邮箱、授权码、内网地址）

优先级:  环境变量  >  配置文件  >  调用时传入的 default

用法:
    from config import get, get_int, get_bool, get_path

    BACKUP_ROOT = get_path("BACKUP_ROOT", "/tmp/PhotoBackup")
    WINDOW      = get_int("EXPORT_WINDOW_MONTHS", 3)
    EMAIL_ON    = get_bool("EMAIL_ENABLED", False)

两个配置文件都允许缺失。
"""

import os
import re
import sys
from pathlib import Path

# modules/utils/config.py → 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
_CONFIG_FILES = ("config.env", "secrets.env")

_ASSIGN_RE = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _strip_value(raw: str) -> str:
    """去掉首尾空白；带引号则只取引号内；不带引号则砍掉行尾注释。

    关键：不能简单地在 '#' 处截断 —— 授权码/密码里可能含 '#'。
    所以先判断是否以引号开头，是则按引号配对取值。
    """
    raw = raw.strip()
    if not raw:
        return ""

    quote = raw[0]
    if quote in ("'", '"'):
        end = raw.find(quote, 1)
        return raw[1:end] if end != -1 else raw[1:]

    # 无引号：空格 + # 之后算注释
    hash_pos = raw.find(" #")
    if hash_pos != -1:
        raw = raw[:hash_pos]
    return raw.strip()


def _parse_file(path: Path) -> dict:
    values = {}
    if not path.is_file():
        return values
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return values

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _ASSIGN_RE.match(line)
        if not m:
            continue
        values[m.group(1)] = _strip_value(m.group(2))
    return values


def _expand(values: dict) -> dict:
    """解析值里的 ${VAR} 引用，例如 REMOTE_115_DIR="${CLOUDDRIVE_MOUNT}/PhotoBackup"。

    最多迭代 5 轮，避免互相引用造成死循环。
    """

    def lookup(name: str):
        if name in os.environ:
            return os.environ[name]
        return values.get(name)

    for _ in range(5):
        changed = False
        for key, val in values.items():
            if "${" not in val:
                continue
            new = _REF_RE.sub(lambda m: lookup(m.group(1)) or "", val)
            if new != val:
                values[key] = new
                changed = True
        if not changed:
            break
    return values


_FILE_VALUES = _expand(
    {k: v for f in _CONFIG_FILES for k, v in _parse_file(CONFIG_DIR / f).items()}
)


def get(key: str, default=None):
    """读取配置项（字符串）。环境变量优先于配置文件。"""
    if key in os.environ:
        return os.environ[key]
    return _FILE_VALUES.get(key, default)


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(str(get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def get_bool(key: str, default: bool = False) -> bool:
    return str(get(key, default)).strip().lower() in ("1", "true", "yes", "on")


def get_path(key: str, default=None):
    """读取路径型配置，返回 Path；未配置且无默认值时返回 None。

    配置文件里写的相对路径按项目根解析（便于用 "config/device-models.json" 这种写法）。
    """
    raw = get(key, default)
    if raw is None:
        return None
    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    return p


def require_path(key: str) -> Path:
    """读取必填路径配置，缺失则报错退出（避免用硬编码路径兜底导致静默故障）。"""
    p = get_path(key)
    if p is None:
        print(f"❌ 未配置 {key}，请检查 config/config.env", file=sys.stderr)
        raise SystemExit(2)
    return p
