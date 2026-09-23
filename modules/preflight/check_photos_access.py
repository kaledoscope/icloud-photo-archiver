#!/usr/bin/env python3.11
"""
check_photos_access.py — 检查当前 Python 解释器能否读取 Photos 库

为什么单独一个模块：macOS 的 TCC（透明度、隐私与安全）权限是绑在
**具体的解释器二进制**上的，不是绑在脚本或用户上。这台机器上有两个
python3.11，只有在「系统设置 → 隐私与安全性 → 全磁盘访问权限」里被
显式添加过的那个才能读 Photos 库。

权限缺失时的表现是 osxphotos 抛权限错误，看起来非常像代码 bug。
所以流水线开跑之前先单独确认一次，把"权限问题"和"代码问题"分开。

为什么用弹出窗口授权不行：TCC 的临时授权不持久，launchd 半夜调起时
那条授权链是断的。必须手动进系统设置添加。

退出码:
    0  可读，或路径不存在等"与本检查无关"的情况（不阻塞流水线）
    1  明确是权限不足（需要用户去系统设置处理）
"""

import os
import sys
from pathlib import Path

from modules.utils.config import get_path

# Photos 库默认位置；不同用户可以改 config
PHOTOS_LIBRARY = get_path(
    "PHOTOS_LIBRARY_PATH",
    Path.home() / "Pictures" / "Photos Library.photoslibrary",
)


def main() -> int:
    lib = str(PHOTOS_LIBRARY)
    interpreter = sys.executable

    if not PHOTOS_LIBRARY.exists():
        # 库不在默认位置 —— 可能是路径配错了，也可能 Photos 从未启用。
        # 这跟 TCC 权限无关，不作为阻塞条件。
        print(f"⚠️  Photos 库不存在: {lib}")
        print(f"   → 若路径不同，请在 config/config.env 设置 PHOTOS_LIBRARY_PATH")
        return 0

    try:
        os.listdir(lib)
    except PermissionError:
        print(f"⚠️  {interpreter} 无法访问 Photos 库（需 Full Disk Access）")
        print(f"   → 系统设置 → 隐私与安全性 → 全磁盘访问权限 → 添加")
        print(f"   → {interpreter}")
        print(f"   ⚠️  弹窗授权不持久，必须手动在系统设置中添加！")
        return 1
    except OSError as e:
        # 其他 I/O 错误（网络卷未挂载、库正在被 Photos.app 重建等）
        # 不阻塞 —— 后续步骤自己会失败并给出更具体的错误。
        print(f"⚠️  Photos 库暂时不可读: {e}")
        return 0

    print(f"✅ {interpreter} 可访问 Photos 库")
    return 0


if __name__ == "__main__":
    sys.exit(main())
