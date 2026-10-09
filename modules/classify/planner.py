#!/usr/bin/env python3.11
"""
planner.py — 五级路径构建与冲突处理

分类树的形状在这里定义，全项目唯一：

    {output}/{设备型号}/{YYYY}/{MM}/{来源}/{photos|videos}/{文件名}

第一性原理：家庭场景中机型 = 人，最外层按「谁拍的」组织，
每人一个顶层文件夹，115 上可以直接按人分享。

冲突处理在内存里完成，不落盘——这样扫描阶段就能产出完整计划供审阅，
不需要先写一堆文件出来才知道会撞名。
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

# 体积格式化统一由 utils 提供；在此转出，便于调用方从 planner 一处取
from modules.utils.files import human_size  # noqa: F401

# 来源只有两种；任意文件夹场景推不出共享相册信息，统一 personal
SOURCE_PERSONAL = "personal"
SOURCE_FAMILY = "family-shared"

MEDIA_PHOTOS = "photos"
MEDIA_VIDEOS = "videos"


class PathConflict(Exception):
    """输入/输出目录关系非法。"""


@dataclass
class PlanEntry:
    """一条「这个文件复制到那里」的记录。"""
    src: Path
    rel: Path                  # 相对输出根目录
    kind: str                  # photo | video
    role: str                  # media | sidecar
    device: str
    model: Optional[str]
    taken_at: Optional[datetime]
    date_source: str           # exif | mtime | unknown
    size_bytes: int

    def to_json(self) -> dict:
        return {
            "src": str(self.src),
            "rel": str(self.rel),
            "kind": self.kind,
            "role": self.role,
            "device": self.device,
            "model": self.model,
            "taken_at": self.taken_at.isoformat() if self.taken_at else None,
            "date_source": self.date_source,
            "size_bytes": self.size_bytes,
        }

    @staticmethod
    def from_json(d: dict) -> "PlanEntry":
        taken = d.get("taken_at")
        return PlanEntry(
            src=Path(d["src"]),
            rel=Path(d["rel"]),
            kind=d.get("kind", "unknown"),
            role=d.get("role", "media"),
            device=d.get("device", ""),
            model=d.get("model"),
            taken_at=datetime.fromisoformat(taken) if taken else None,
            date_source=d.get("date_source", "unknown"),
            size_bytes=int(d.get("size_bytes", 0)),
        )


def validate_dirs(input_dir: Path, output_dir: Path) -> None:
    """拒绝互为包含的输入/输出目录。

    输出目录在输入目录里面 → 扫描会把自己刚拷进去的文件再扫一遍；
    反过来 → 输出会覆盖源素材所在的位置。两种都必须是硬错误。
    """
    src = Path(input_dir).resolve()
    dst = Path(output_dir).resolve()

    if src == dst:
        raise PathConflict("输入目录和输出目录是同一个目录")
    if src in dst.parents:
        raise PathConflict(f"输出目录位于输入目录内部，会造成自我递归拷贝：\n  输入 {src}\n  输出 {dst}")
    if dst in src.parents:
        raise PathConflict(f"输入目录位于输出目录内部，可能覆盖源素材：\n  输入 {src}\n  输出 {dst}")


def dedupe_relative(
    rel: Path,
    taken_at: Optional[datetime],
    is_taken: Callable[[Path], bool],
) -> Path:
    """重名处理规则，全项目唯一实现。

    先追加拍摄时间戳（历史行为），仍冲突再加 _1 / _2 递增序号。
    is_taken 由调用方提供：分类计划传「计划内已占用 + 输出目录已存在」，
    Photos 导出传「目标文件是否已存在」。
    """
    if not is_taken(rel):
        return rel

    stem, suffix, parent = rel.stem, rel.suffix, rel.parent

    if taken_at is not None:
        stamped = f"{stem}_{taken_at.strftime('%Y%m%d_%H%M%S')}"
        candidate = parent / f"{stamped}{suffix}"
        if not is_taken(candidate):
            return candidate
        stem = stamped

    n = 1
    while True:
        candidate = parent / f"{stem}_{n}{suffix}"
        if not is_taken(candidate):
            return candidate
        n += 1


def build_relative_path(
    device: str,
    taken_at: Optional[datetime],
    source: str,
    media_type: str,
    filename: str,
) -> Path:
    """五级相对路径。时间缺失时归到 unknown 年月，不猜。"""
    year = taken_at.strftime("%Y") if taken_at else "unknown-year"
    month = taken_at.strftime("%m") if taken_at else "unknown-month"
    return Path(device) / year / month / source / media_type / filename


class PlanBuilder:
    """累积计划条目，并保证同一输出目录内不会有两条记录抢同一个文件名。

    冲突时沿用既有规则追加拍摄时间戳（photo_export.py 一直是这么做的），
    仍然撞名再加 _1 / _2 递增序号——绝不覆盖已存在的文件。
    """

    def __init__(self, output_root: Optional[Path] = None):
        self.output_root = Path(output_root) if output_root else None
        self.entries: List[PlanEntry] = []
        self.skipped: List[dict] = []
        self.failed = 0
        self._reserved: Set[str] = set()

    def skip(self, src: Path, reason: str) -> None:
        self.skipped.append({"src": str(src), "reason": reason})

    def _taken(self, rel: Path) -> bool:
        """该相对路径是否已被占用（本计划内，或输出目录里已存在）。"""
        key = str(rel)
        if key in self._reserved:
            return True
        if self.output_root is not None:
            try:
                return (self.output_root / rel).exists()
            except OSError:
                return False
        return False

    def _dedupe(self, rel: Path, taken_at: Optional[datetime]) -> Path:
        """给撞名的文件找一个不冲突的相对路径。"""
        return dedupe_relative(rel, taken_at, self._taken)

    def add_media(
        self,
        src: Path,
        device: str,
        model: Optional[str],
        taken_at: Optional[datetime],
        date_source: str,
        media_type: str,
        source: str = SOURCE_PERSONAL,
    ) -> PlanEntry:
        rel = build_relative_path(device, taken_at, source, media_type, src.name)
        rel = self._dedupe(rel, taken_at)
        self._reserved.add(str(rel))
        entry = PlanEntry(
            src=src, rel=rel,
            kind="video" if media_type == MEDIA_VIDEOS else "photo",
            role="media", device=device, model=model, taken_at=taken_at,
            date_source=date_source, size_bytes=_size_of(src),
        )
        self.entries.append(entry)
        return entry

    def add_sidecar(self, src: Path, beside: PlanEntry) -> PlanEntry:
        """旁车文件（.aae）放到主文件旁边，同名冲突同样处理。"""
        rel = beside.rel.parent / src.name
        rel = self._dedupe(rel, beside.taken_at)
        self._reserved.add(str(rel))
        entry = PlanEntry(
            src=src, rel=rel, kind="sidecar", role="sidecar", device=beside.device,
            model=beside.model, taken_at=beside.taken_at,
            date_source=beside.date_source, size_bytes=_size_of(src),
        )
        self.entries.append(entry)
        return entry

    def summary(self) -> Dict:
        """给界面用的统计：按设备、按类型、总量、以及需要人工注意的地方。"""
        by_device: Dict[str, Dict[str, int]] = {}
        by_kind: Dict[str, int] = {}
        by_date_source: Dict[str, int] = {}
        total_bytes = 0

        for e in self.entries:
            d = by_device.setdefault(e.device, {"photos": 0, "videos": 0, "sidecars": 0})
            if e.role == "sidecar":
                d["sidecars"] += 1
            elif e.kind == "video":
                d["videos"] += 1
            else:
                d["photos"] += 1

            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
            by_date_source[e.date_source] = by_date_source.get(e.date_source, 0) + 1
            total_bytes += e.size_bytes

        skipped_by_reason: Dict[str, int] = {}
        for s in self.skipped:
            skipped_by_reason[s["reason"]] = skipped_by_reason.get(s["reason"], 0) + 1

        return {
            "files": len(self.entries),
            "total_bytes": total_bytes,
            "by_device": by_device,
            "by_kind": by_kind,
            "by_date_source": by_date_source,
            "skipped": len(self.skipped),
            "skipped_by_reason": skipped_by_reason,
        }


def _size_of(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0
