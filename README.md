# O-Data-Center-System

> 个人数据中心：iPhone 照片视频 → 自动分类 → 异地双备份 → 自动清理，全链路无人值守。

拍了照片就忘掉它——你的 Mac Mini 会帮你整理、备份到 115 网盘和群晖 NAS，然后清理本地中转空间。

---

## 核心文档

| 文档 | 内容 |
|------|------|
| [REQUIREMENTS.md](./REQUIREMENTS.md) | 需求规格：从第一性原理拆解，功能/非功能需求，边界情况 |
| [IMPLEMENTATION.md](./IMPLEMENTATION.md) | 实现文档：架构决策、设计取舍、故障排查 |
| [steps/](./steps/) | 分步操作手册（step-00 ~ step-06） |

---

## 项目结构

```
O-Data-Center-System/
│
├── modules/                            # 🧩 可独立运行的模块
│   ├── export/
│   │   ├── photo_export.py             #   核心：导出 + 五级分类
│   │   └── verify_backup.py            #   备份完整性验证
│   ├── backup/
│   │   ├── 115/backup_to_115.sh        #   rsync → 115（异地热备）
│   │   └── nas/backup_to_nas.sh        #   WOL + rsync → NAS（本地冷备）
│   ├── cleanup/                        #   本地中转清理（待迁入）
│   ├── scheduler/
│   │   ├── photo_backup_weekly.sh      #   周度主流水线
│   │   └── launchd/                    #   调度配置（2 个 job）
│   └── utils/                          #   logger · notify · email · watchdog
│
├── config/
│   ├── config.env                      #   用户配置（gitignored）
│   └── device-models.json              #   设备型号 → 机主映射
│
├── steps/                              # 📚 分步操作手册
│   ├── step-00-environment/            #   环境准备（一次性）
│   ├── step-01-icloud-sync/            #   iCloud → Mac 同步
│   ├── step-02-photo-export/           #   照片导出与五级分类 ⭐
│   ├── step-03-backup-115/             #   备份至 115 网盘
│   ├── step-04-backup-nas/             #   备份至群晖 NAS（冷备）
│   └── step-06-scheduling/             #   全自动调度 (launchd)
│
└── .state/ · .logs/                    # 运行状态与日志（gitignored）
```

---

## 数据流

```
iPhone → iCloud → Mac Mini → 五级分类 → ┬→ 115 网盘 (CloudDrive2, 异地热备)
                                        └→ 群晖 NAS (WOL 冷备, 每月)

每周一 / 周四 03:00   自动导出 → 备份 → 清理本地中转
```

> ⚠️ **iCloud / iPhone 的空间释放目前是手动的。**
> 流水线只生成「可清理清单」并发邮件，不自动删除（macOS 已封锁脚本删除 Photos 的接口）。

---

## 五级分类 ⭐

从第一性原理出发：**家庭场景中机型 = 人，最外层按「谁拍的」组织。**

```
PhotoBackup/
└── {device-model}/              # 谁拍的（顶层入口，一人一个文件夹）
    └── YYYY/                    # 年份
        └── MM/                  # 月份
            └── {source}/        # 来源: personal | family-shared
                ├── photos/      # 照片
                └── videos/      # 视频
```

**设备型号即人员分类**：

```
iPhone_15_Pro/2026/06/personal/photos/      ← 设备 A 拍的照片
Pixel_8/2026/06/personal/photos/  ← 设备 B 拍的照片
iPhone_13/2026/06/family-shared/videos/     ← 共享相簿里的视频
Unknown_Device/2026/06/personal/photos/     ← 无法识别设备
```

分类依据是 **EXIF 相机型号**，映射表在 `config/device-models.json` —— 不限于 Apple 设备，任何相机型号都能登记归类，未登记的按型号原样归档。

**115 网盘视角**：一人一个顶层文件夹，可直接按人分享：

```
/Volumes/CloudDrive/PhotoBackup/
├── iPhone_15_Pro/          ← 分享给 A
├── Pixel_8/      ← 分享给 B
└── iPhone_13/              ← 分享给 C
```

---

## 3-2-1 备份

- **3** 份副本：iPhone 原件 + Mac 本地 + 115 / NAS 各一
- **2** 种介质：SSD (Mac) + HDD (NAS) + 云 (115)
- **1** 份异地：115 网盘

---

## 开始使用

按 `steps/` 顺序操作：

```
step-00-environment/     ← 从这里开始（一次性环境配置）
step-01-icloud-sync/     ← iCloud → Mac 同步
step-02-photo-export/    ← 核心：理解五级分类
step-03-backup-115/
step-04-backup-nas/
step-06-scheduling/      ← 全自动调度
```

常用命令：

```bash
# 配置
vim config/config.env                  # 用户配置
vim config/device-models.json          # 设备型号 → 机主（可选）

# 分模块测试
python3.11 modules/export/photo_export.py --dry-run    # 预览导出
bash modules/backup/115/backup_to_115.sh               # 测试 115 备份
bash modules/backup/nas/backup_to_nas.sh --wol-only    # 测试 NAS 唤醒

# 完整流水线
bash modules/scheduler/photo_backup_weekly.sh
```

部署定时任务：

```bash
# 1. 替换 plist 中的用户名占位符
# 2. 部署
cp modules/scheduler/launchd/*.plist ~/Library/LaunchAgents/
for j in ~/Library/LaunchAgents/com.datacenter.*.plist; do
    launchctl load "$j"
done

# 3. 验证
launchctl list | grep com.datacenter
```

---

## 模块化说明

各模块完全独立，可单独运行、单独替换：

| 模块 | 路径 | 独立性 |
|------|------|--------|
| 照片导出 | `modules/export/` | 可单独运行，不依赖备份模块 |
| 115 备份 | `modules/backup/115/` | 可单独运行，不依赖 NAS 模块 |
| NAS 备份 | `modules/backup/nas/` | 可单独运行，不依赖 115 模块 |
| 调度 | `modules/scheduler/` | 编排以上模块 |

**替换示例**：如果将来改用 rclone 同步到 115，只需替换 `modules/backup/115/backup_to_115.sh`，其他模块不受影响。

---

## 技术栈

- **照片导出**: [osxphotos](https://github.com/RhetTbull/osxphotos) (Python)
- **差异同步**: rsync (macOS 内置)
- **115 挂载**: CloudDrive2
- **NAS 冷备**: wakeonlan + rsync + SSH
- **调度**: macOS launchd
- **通知**: osascript (原生通知中心) + SMTP 邮件

---

## 许可

个人项目，自用为主。
