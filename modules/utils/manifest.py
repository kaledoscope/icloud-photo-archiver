#!/usr/bin/env python3.11
"""
manifest.py — 备份清单的统一读写与查询

清单回答两个问题：本地有哪些文件、它们备份到哪儿了。
115 脚本要标记自己那份，NAS 脚本也要标记自己那份，清理和验证又要按标记查——
与其各处抄一遍，不如集中在这里。

关键设计：**备份目标的名字是参数，不是硬编码。**
新增一个备份目标 = 往 config 的 BACKUP_TARGETS 里加一个词，
不需要改任何逻辑代码。

清单结构::

    {
      "files": {
        "<相对 BACKUP_ROOT 的路径>": {
          "sha256": "...", "size_bytes": 123,
          "original_asset_uuid": "...", "exported_at": "...",
          "source": "...", "device_model": "...", "media_type": "...",
          "backed_up_115": false, "backed_up_nas": false
        }
      },
      "last_115_backup_ts": "...", "last_nas_backup_ts": "..."
    }

用法::

    from modules.utils.manifest import Manifest

    m = Manifest()                    # 路径取自 config 的 BACKUP_ROOT
    m.mark_backed_up("115")           # 把 backed_up_115 全部置 true
    m.unbacked_count(prefix, "115")   # 某个月份目录还有几个没备份
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from modules.utils.config import get, require_path

# 清单里的文件条目
Entry = Dict[str, object]


# ===== 备份目标 =====

def configured_targets() -> List[str]:
    """config 里 BACKUP_TARGETS 声明的备份目标，例如 ["115", "nas"]。"""
    raw = get("BACKUP_TARGETS", "115,nas")
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def cleanup_required_targets() -> List[str]:
    """清理前必须确认已备份的目标（config: CLEANUP_REQUIRE_TARGETS）。

    默认只要求 115。NAS 常年离线，把它设为前置条件会让清理永远不触发。
    """
    raw = get("CLEANUP_REQUIRE_TARGETS", "115")
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def flag(target: str) -> str:
    """备份目标 → 清单里的标记字段名（"115" → "backed_up_115"）。"""
    return f"backed_up_{target}"


def timestamp_key(target: str) -> str:
    """备份目标 → 清单里的时间戳字段名。"""
    return f"last_{target}_backup_ts"


def empty_flags() -> Dict[str, bool]:
    """新建文件条目时的初始标记：所有已声明的目标都置 false。"""
    return {flag(t): False for t in configured_targets()}


# ===== 路径 → 年月 =====

def file_ym_from_path(relative_path: str) -> Optional[Tuple[int, int]]:
    """从清单路径提取拍摄年月。路径格式: 设备/年/月/..."""
    parts = relative_path.split("/")
    if len(parts) >= 3:
        try:
            return (int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            pass
    return None


def cutoff_ym(months: int) -> Tuple[int, int]:
    """N 个月前对应的 (年, 月)。"""
    now = datetime.now()
    total = now.year * 12 + (now.month - 1) - months
    return (total // 12, (total % 12) + 1)


class Manifest:
    """备份清单。一次 load，内存里改，save 落盘。

    默认路径: $BACKUP_ROOT/.state/backup_manifest.json
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        if path is None:
            path = require_path("BACKUP_ROOT") / ".state" / "backup_manifest.json"
        self.path: Path = Path(path)
        self._data: Optional[dict] = None
        self._uuid_index: Optional[Dict[str, Entry]] = None

    # ---- 读写 ----

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> dict:
        """读清单。文件不存在或损坏时返回空清单（不抛异常）。"""
        if self._data is not None:
            return self._data

        data: dict = {}
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError, OSError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("files", {})
        for target in configured_targets():
            data.setdefault(timestamp_key(target), None)

        self._data = data
        return data

    def save(self) -> None:
        """落盘。父目录不存在会自动创建。"""
        if self._data is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ---- 查询 ----

    @property
    def files(self) -> Dict[str, Entry]:
        return self.load()["files"]

    def entry_for(self, relative_path: str) -> Optional[Entry]:
        return self.files.get(relative_path)

    @property
    def _by_uuid(self) -> Dict[str, Entry]:
        """uuid → 条目 的索引，首次访问时构建。

        没有这个索引时，"按 uuid 找条目"要在整个清单里线性扫描，
        Photos 库有几千张照片就是几百万次比较，慢得没必要。
        """
        if self._uuid_index is None:
            index: Dict[str, Entry] = {}
            for entry in self.files.values():
                uuid = entry.get("original_asset_uuid")
                if uuid and uuid not in index:
                    index[str(uuid)] = entry
            self._uuid_index = index
        return self._uuid_index

    def find_by_uuid(self, asset_uuid: str) -> Optional[Entry]:
        return self._by_uuid.get(asset_uuid)

    def under_prefix(self, prefix: str) -> Dict[str, Entry]:
        """路径以 prefix 开头的所有条目（用于按月份目录聚合）。"""
        return {p: e for p, e in self.files.items() if p.startswith(prefix)}

    # ---- 标记 ----

    @staticmethod
    def is_backed_up(entry: Entry, target: str) -> bool:
        return bool(entry.get(flag(target), False))

    def mark_backed_up(self, target: str, only_paths: Optional[List[str]] = None) -> int:
        """把某个备份目标的标记全部置 true，并记录时间戳。

        only_paths 给定则只标记这些路径（rsync 只传了一部分时用）。
        返回被标记的条目数。
        """
        data = self.load()
        targets = only_paths if only_paths is not None else list(self.files.keys())
        marked = 0
        for path in targets:
            entry = self.files.get(path)
            if entry is None:
                continue
            entry[flag(target)] = True
            marked += 1

        if marked:
            data[timestamp_key(target)] = datetime.now().isoformat()
            self._uuid_index = None  # 条目内容变了，索引作废（防御性）
        return marked

    def unbacked_count(self, prefix: str, target: str) -> int:
        """某个路径前缀下，尚未确认备份到 target 的文件数。"""
        return sum(
            1 for entry in self.under_prefix(prefix).values()
            if not self.is_backed_up(entry, target)
        )

    # ---- 风险文件（清理前的安全检查对象）----

    def at_risk(self, cutoff_months: int, backup_root: Path) -> List[str]:
        """即将被清理、因而必须先确认已归档的文件。

        两个条件缺一不可：
        1. 拍摄日期早于 cutoff —— 会被清理逻辑盯上
        2. 磁盘上确实存在 —— 已清理的幽灵条目不需要验证
        """
        limit = cutoff_ym(cutoff_months)
        at_risk: List[str] = []
        for path in self.files:
            file_ym = file_ym_from_path(path)
            if file_ym is None or file_ym >= limit:
                continue
            if not (backup_root / path).exists():
                continue
            at_risk.append(path)
        return at_risk

    def ghost_count(self, cutoff_months: int, backup_root: Path) -> int:
        """清单里已过期、但磁盘上已不存在的条目数（清理后的残留记录）。"""
        limit = cutoff_ym(cutoff_months)
        ghosts = 0
        for path in self.files:
            file_ym = file_ym_from_path(path)
            if file_ym is None or file_ym >= limit:
                continue
            if not (backup_root / path).exists():
                ghosts += 1
        return ghosts


def iter_entries(manifest: dict) -> Iterator[Entry]:
    """遍历清单里的所有文件条目（不关心路径时用）。"""
    return iter(manifest.get("files", {}).values())


# ===== 命令行入口 =====
# shell 侧备份脚本用它打标记，避免在 shell 里内联 Python。

def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m modules.utils.manifest",
        description="备份清单的读写入口",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_mark = sub.add_parser("mark", help="把某个备份目标的标记置为已备份")
    p_mark.add_argument("--target", required=True,
                        help="备份目标名，例如 115 / nas")
    p_mark.add_argument("--paths-from", default=None,
                        help="只标记此文件里列出的路径（每行一个）；不给则标记全部")
    p_mark.add_argument("--dry-run", action="store_true",
                        help="只报告会标记多少条，不落盘")

    p_show = sub.add_parser("show", help="显示清单概况")
    p_show.add_argument("--target", default=None, help="只看某个目标的已备份数")

    args = parser.parse_args()

    m = Manifest()
    if not m.exists:
        print(f"⚠️  清单文件不存在: {m.path}")
        return 1 if args.cmd == "mark" else 0

    if args.cmd == "show":
        total = len(m.files)
        print(f"清单: {m.path}")
        print(f"文件条目: {total}")
        for target in configured_targets():
            done = sum(1 for e in m.files.values() if m.is_backed_up(e, target))
            ts = m.load().get(timestamp_key(target)) or "从未"
            print(f"  {target:>6}: {done}/{total} 已备份   最后备份时间: {ts}")
        return 0

    # cmd == mark
    paths = None
    if args.paths_from:
        try:
            with open(args.paths_from, encoding="utf-8") as fh:
                paths = [line.strip() for line in fh if line.strip()]
        except OSError as e:
            print(f"❌ 无法读取路径清单: {e}", file=sys.stderr)
            return 1

    if args.dry_run:
        count = len(paths) if paths is not None else len(m.files)
        print(f"[DRY RUN] 将标记 {count} 个文件为 {args.target} 已备份")
        return 0

    marked = m.mark_backed_up(args.target, only_paths=paths)
    m.save()
    print(f"已标记 {marked} 个文件为 {args.target} 已备份")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
