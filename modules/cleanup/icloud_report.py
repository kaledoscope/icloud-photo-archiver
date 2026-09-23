#!/usr/bin/env python3.11
"""
icloud_report.py — 生成「Photos.app / iCloud 里哪些旧照片可以删了」的报告

为什么是报告而不是自动删除：
    macOS Sequoia 的 Photos 10.0 封掉了 AppleScript / JXA 的删除接口，
    脚本无法再删照片。所以这一步退化成报告 —— 列出各年月已完整备份的比例，
    用户自己在 Photos.app 里 Cmd+A 删除。

    这一步的价值没有消失：它把"删哪些是安全的"变成了一个确定的清单，
    而不是让用户凭感觉删。删掉之后 iCloud 和 iPhone 的空间会跟着释放。

判定逻辑:
    Photos 库里拍摄日期早于 cutoff 的照片 → 逐张核对清单 → 是否已确认备份

放在导出之前跑：否则刚删掉的照片下一轮又会被导出回来。

退出码:
    0  报告已生成
    1  无法读取 Photos 库
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from modules.utils.config import get_int, require_path
from modules.utils.manifest import Manifest, cleanup_required_targets, flag

try:
    import osxphotos  # noqa: F401
    from osxphotos import PhotosDB
except ImportError:
    print("❌ osxphotos 未安装。请执行: pip3 install osxphotos")
    raise SystemExit(2)

# 未确认原因的分类
REASON_NOT_EXPORTED = "不在清单中（从未导出）"
REASON_NOT_BACKED = "在清单中，但尚未确认备份"


def _as_aware(dt):
    """把 naive datetime 补上本地时区。

    osxphotos 的 photo.date 带时区、date_added 有时不带；两者直接比较会抛
    TypeError（naive 与 aware 不能相减）。这里是历史上踩过的坑。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.astimezone()
    return dt


def collect(manifest: Manifest, cutoff: datetime, targets: List[str]) -> Dict:
    """扫描 Photos 库，按年月统计备份情况。"""
    try:
        photosdb = PhotosDB()
    except Exception as e:
        print(f"❌ 无法读取 Photos 库: {e}")
        raise SystemExit(1)

    month_total: Dict[str, int] = defaultdict(int)
    month_backed: Dict[str, int] = defaultdict(int)
    unconfirmed: List[Tuple[str, str, str, str]] = []  # (年月, 文件名, 拍摄时间, 原因)
    in_window = 0

    for photo in photosdb.photos():
        dt = _as_aware(photo.date) or _as_aware(photo.date_added)
        if dt is None:
            continue
        if dt > cutoff:
            in_window += 1
            continue

        ym = dt.strftime("%Y-%m")
        month_total[ym] += 1

        entry = manifest.find_by_uuid(str(photo.uuid)) if photo.uuid else None
        if entry is None:
            unconfirmed.append((ym, photo.original_filename, str(dt), REASON_NOT_EXPORTED))
        elif all(entry.get(flag(t), False) for t in targets):
            month_backed[ym] += 1
        else:
            missing = [t for t in targets if not entry.get(flag(t), False)]
            unconfirmed.append((
                ym, photo.original_filename, str(dt),
                f"{REASON_NOT_BACKED} ({', '.join(missing)})",
            ))

    return {
        "month_total": dict(month_total),
        "month_backed": dict(month_backed),
        "unconfirmed": unconfirmed,
        "in_window": in_window,
    }


def render(stats: Dict, months: int, targets: List[str]) -> str:
    """把统计结果排版成人类可读的报告。"""
    month_total = stats["month_total"]
    month_backed = stats["month_backed"]
    unconfirmed = stats["unconfirmed"]
    target_label = " + ".join(targets)

    buf: List[str] = [""]
    buf.append("━━━ iCloud/Photos 可清理报告 ━━━")
    buf.append(f"截止线: {months} 个月前（早于此日期的照片纳入检查）")
    buf.append("")
    buf.append(f"判定逻辑: Photos 库中早于 cutoff 的照片 → 核对清单 → 确认 {target_label} 备份状态")
    buf.append("")

    fully_ready: List[Tuple[str, int]] = []
    partially_ready: List[Tuple[str, int, int, int]] = []
    not_ready: List[Tuple[str, int]] = []

    for ym in sorted(month_total):
        total = month_total[ym]
        backed = month_backed.get(ym, 0)
        label = f"{ym[:4]}年{ym[5:]}月"
        if total == backed:
            fully_ready.append((label, total))
        elif backed > 0:
            partially_ready.append((label, backed, total, total - backed))
        else:
            not_ready.append((label, total))

    if fully_ready:
        buf.append(f"✅ 已完整备份至{target_label}，可在 Photos.app 中手动删除:")
        for label, cnt in fully_ready:
            buf.append(f"   {label}  —  {cnt}/{cnt} 全部确认")
        buf.append("")
        buf.append(f"   操作: 打开 Photos.app → 搜「{fully_ready[0][0]}」→ Cmd+A → Delete")
        buf.append("         (照片进入「最近删除」→ 30 天后彻底清除)")
        buf.append("")

    if partially_ready:
        buf.append("━" * 40)
        buf.append("⚠️  部分未确认 — 不建议整体删除:")
        for label, backed, total, missing in partially_ready:
            buf.append(f"   {label}: {backed}/{total} 已备份, {missing} 项未确认")
        buf.append("")
        buf.append("─── 未确认文件详情 ───")
        buf.append("")
        for ym, fname, dt_str, reason in unconfirmed:
            buf.append(f"   ❓ {fname}")
            buf.append(f"      拍摄日期: {dt_str}")
            buf.append(f"      原因: {reason}")
            buf.append("")

    if not_ready:
        buf.append(f"⏭  无备份记录（不建议清理）:")
        for label, cnt in not_ready:
            buf.append(f"   {label}  —  {cnt} 项")
        buf.append("")

    if not fully_ready and not partially_ready:
        buf.append("⚠️  没有发现已完整备份的旧月份")

    buf.append(f"截止线内（保留）: {stats['in_window']} 项 | 检查范围: {sum(month_total.values())} 项")
    return "\n".join(buf)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 iCloud/Photos 可清理报告")
    parser.add_argument("--months", type=int, default=None,
                        help="保留月数（默认取 config 的 PHOTOS_APP_RETENTION_MONTHS）")
    args = parser.parse_args()

    backup_root = require_path("BACKUP_ROOT")
    months = args.months if args.months is not None else get_int("PHOTOS_APP_RETENTION_MONTHS", 3)
    targets = cleanup_required_targets()

    # 没有前置目标时 all(...) 恒为 True，会把每张照片都判成"已备份"，
    # 进而建议用户删光 iCloud —— 这是最危险的失效模式，直接拒绝出报告。
    if not targets:
        print("❌ CLEANUP_REQUIRE_TARGETS 为空 —— 无法判定备份状态，")
        print("   报告会把所有照片误判为已备份。已中止。")
        print('   请在 config/config.env 中设置，例如 CLEANUP_REQUIRE_TARGETS="115"')
        return 1

    manifest = Manifest(backup_root / ".state" / "backup_manifest.json")
    if not manifest.exists:
        print("❌ 清单文件不存在，无法判定备份状态")
        return 1

    cutoff = datetime.now().astimezone() - timedelta(days=months * 30)
    stats = collect(manifest, cutoff, targets)
    print(render(stats, months, targets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
