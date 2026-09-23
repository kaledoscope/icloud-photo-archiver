# O-Data-Center-System — 实现文档

> **本文只写「为什么这么设计」** —— 架构决策、设计取舍、故障排查。
>
> - 怎么装、怎么配、怎么跑 → **[`steps/`](./steps/)** 分步手册
> - 每段代码干什么 → **代码本身**（本文不再逐字复制脚本，避免与源码不同步）
> - 需求与边界情况 → **[REQUIREMENTS.md](./REQUIREMENTS.md)**

---

## 目录

1. [工具选型](#1-工具选型)
2. [系统架构](#2-系统架构)
3. [流水线设计](#3-流水线设计)
4. [关键设计取舍](#4-关键设计取舍)
5. [配置](#5-配置)
6. [故障排查](#6-故障排查)
7. [附录](#附录-a-clouddrive2-与-rclone-方案对比)

---

## 1. 工具选型

### 1.1 核心工具矩阵

| 环节 | 工具 | 为什么选它 | 替代方案 |
|------|------|-----------|----------|
| **照片导出** | [osxphotos](https://github.com/RhetTbull/osxphotos) | 唯一成熟的 Photos.app CLI 工具，支持增量导出、相簿过滤、元数据保留 | `exiftool`（太底层）、AppleScript（不稳定） |
| **差异同步** | `rsync` (macOS 内置) | 工业级差异同步，`--partial` 断点续传，零依赖 | `rclone`（更重，但支持更多后端） |
| **115 挂载** | [CloudDrive2](https://www.clouddrive2.com) | 将 115 网盘挂载为本地目录，rsync 可直接操作 | `rclone` + 115 backend、OpenList/AList |
| **NAS 冷备** | `wakeonlan` + `rsync` + `ssh` | 标准 Unix 工具组合，零额外依赖 | Synology Drive（闭源，不易脚本化） |
| **调度器** | macOS `launchd` | 系统原生，开机自启，资源占用低，日志天然集成 | `cron`（macOS 已弃用）、`Hammerspoon` |
| **通知** | `osascript` + Python `email` | 原生通知中心 + 邮件长报告，无需额外 app | `terminal-notifier`（需 brew 安装） |
| **脚本语言** | Python 3.11 + Bash | osxphotos 是 Python 库；bash 胶水适合管道操作 | 全部 bash（复杂度吃不消） |

### 1.2 为什么不选 rclone 做主同步

rclone 功能很强，但在本系统里 **CloudDrive2 挂载 + rsync** 更合适：

- CloudDrive2 已经常驻维护 115 连接，没必要再引入 rclone 的 115 backend
- rsync 面对的是**本地文件系统到本地文件系统**（挂载点），不经过网络层，一次写入即完成
- rclone 的 115 backend 依赖 cookie 维护，多一套认证要管

> **但这不等于 rclone 没有价值。** 如果你日后想摆脱 CloudDrive2 的付费限制，
> rclone 原生 115 backend 是现成的替换路径 —— 详见 [附录 A](#附录-a-clouddrive2-与-rclone-方案对比)。

---

## 2. 系统架构

### 2.1 数据流

```
iPhone 拍照
    │ iCloud Photos 自动上传
    ▼
iCloud（Apple 数据中心）
    │ 「将原片下载到此 Mac」
    ▼
┌───────────────────────── Mac Mini ─────────────────────────┐
│                                                            │
│  Step 0  验证老文件已在 115 归档 ──── 失败则中止（FATAL）    │
│  Step 1  清理本地过期中转（>3 个月 且 已确认 115 → 废纸篓）  │
│  Step 2  生成 iCloud 可清理报告（只报告，不删）              │
│  Step 3  osxphotos 增量导出 → 五级分类                       │
│  Step 4  rsync → CloudDrive2 挂载点 → 115 网盘（异地热备）   │
│  Step 5  WOL + rsync → 群晖 NAS（本地冷备，每月第一周周一）  │
│  Step 6  邮件报告 + macOS 通知                              │
│                                                            │
│  每周一 / 周四 03:00 由 launchd 自动触发                     │
└────────────────────────────────────────────────────────────┘

⚠️ iCloud / iPhone 的空间释放是【手动】的 —— 见 4.5
```

### 2.2 五级分类

**设计原则（第一性原理）**：家庭场景中「机型 = 人」，最外层按**谁拍的**组织。
每个人打开备份直接看到自己的文件夹，115 上也能按人整目录分享。

```
{device-model}/          # 谁拍的（EXIF 相机型号 → config/device-models.json 映射）
└── YYYY/                # 年
    └── MM/              # 月
        └── {source}/    # 来源: personal | family-shared
            ├── photos/
            └── videos/
```

**为什么用机型而不是相簿名**：相簿是 Apple 生态内的概念，导出后不携带；
而 EXIF 相机型号写在文件本身，**任何工具、任何平台都能读**，不锁定生态。

分类依据是 EXIF，因此**不限于 Apple 设备** —— 任何相机型号都能登记归类，
未登记的按型号原样归档到 `Unknown_Device/` 之外的独立目录，不丢数据。

### 2.3 状态文件设计

```
~/PhotoBackup/.state/
├── last_export.json        # 上次导出时间戳与数量
└── backup_manifest.json    # 每个文件的备份状态（核心）
```

`backup_manifest.json` 的结构：

```json
{
  "files": {
    "iPhone_15_Pro/2026/06/personal/photos/IMG_1234.HEIC": {
      "sha256": "abc123...",
      "exported_at": "2026-06-07T03:05:00",
      "backed_up_115": true,
      "backed_up_nas": false,
      "original_asset_uuid": "XXXX-YYYY-ZZZZ"
    }
  },
  "last_115_backup_ts": "2026-06-07T03:12:00",
  "last_nas_backup_ts": "2026-06-07T03:45:00"
}
```

**关键约定：清理逻辑只认 `backed_up_115` 标记，不认文件日期。**
先备份、后清理 —— 这是整个系统不丢数据的根。

---

## 3. 流水线设计

### 3.1 七步概览

| 步骤 | 做什么 | 失败会怎样 |
|------|--------|-----------|
| **Pre-flight** | CloudDrive2 挂载健康检查、TCC 权限检查、看门狗拉起 | 非致命，仅告警 |
| **Step 0** | 全量验证「即将被 Step 1 清理的老文件」确实已在 115 | **FATAL，中止整个流水线** |
| **Step 1** | 清理本地中转：>3 个月 **且** `backed_up_115=true` → 移到废纸篓 | 非致命，跳过该项 |
| **Step 2** | 生成 iCloud/Photos 可清理清单 | 非致命，仅报告 |
| **Step 3** | 等待 iCloud 同步 → osxphotos 增量导出 + 五级分类 | 非致命，后续步骤跳过同步 |
| **Step 4** | rsync → 115（带重试） | 非致命，但 Step 1 下轮会更保守 |
| **Step 5** | NAS 冷备（仅每月第一周周一） | 非致命 |
| **Step 6** | 邮件报告 + macOS 通知 | 非致命 |

**设计原则：只有「可能导致丢数据」的步骤才允许 FATAL。**
其余步骤一律降级为告警 —— 一次网络抖动不该让整条流水线瘫痪。

### 3.2 门控设计：为什么 Step 0 是 FATAL

Step 1 会删除本地中转目录里的老文件。删之前必须确认它们**已经在 115 上**。

Step 0 **只验证「即将被清理的那一批」**（拍摄日期 > 保留月数的），
而不是全量比对 —— 全量比对要遍历整个挂载点，代价太高，而且大部分文件下轮不删。

```
Step 0 验证通过 → 放行 Step 1
Step 0 验证失败 → FATAL，中止
                  （宁可本地空间不释放，也不能删掉没归档的文件）
```

这是**失败关闭（fail-closed）**：不确定时不动作。
而 Pre-flight 的 CloudDrive2 检查是**失败开放（fail-open）**：挂载点没起来就告警，
但不阻止后续步骤 —— 因为如果连挂载点都没有，rsync 自己会失败，不需要额外拦截。

### 3.3 增量基线：磁盘扫描 vs 状态文件

导出时如何判断「哪些是新照片」？

**选磁盘扫描，不选状态文件。** 理由：

- 磁盘上的文件是**唯一真相**。状态文件可能因为崩溃、手动删文件、迁移而失真
- 状态文件一旦损坏，可能导致**重复导出**（浪费）或**漏导出**（丢数据）
- 扫描 `~/PhotoBackup/` 的目录结构成本很低（秒级），不值得为省这点时间引入失真风险

状态文件（manifest）只用来记录**备份状态**，不用来记录**文件是否存在**。

### 3.4 清理策略

系统里有**两处**清理，性质完全不同：

| 位置 | 对象 | 方式 | 安全性 |
|------|------|------|--------|
| **Step 1**（主干，自动） | 本地中转目录 `~/PhotoBackup/` 的超期文件 | `mv` → `~/.Trash/` | 可恢复 |
| **Step 2**（主干，仅报告） | Photos.app / iCloud 的照片 | 输出清单，**人工执行** | 不自动删 |

**Step 1 为什么用 `mv` 而不是 `rm -rf`**：
废纸篓是最后一道安全网。即使逻辑判断有误，用户还有机会捞回来。
`rm -rf` 没有后悔药 —— 这是本地中转清理与远端同步的本质区别：
远端有 115 和 NAS 两份副本，本地这一份不值得冒不可逆的风险。

---

## 4. 关键设计取舍

### 4.1 rsync 参数

实际使用的参数（两个备份脚本一致）：

| 参数 | 作用 |
|------|------|
| `-a` | 归档模式：保留权限、时间戳、软链接 |
| `-v` | 详细输出 |
| `-h` | 人类可读的文件大小 |
| `-P` | 进度显示 + `--partial`（断点续传） |
| `--partial` | 保留未传完的文件，下次续传 |
| `--exclude` | 排除 `.state/`、`.logs/`、`.DS_Store`、`._*`、`.gitkeep` |
| `--timeout=300` | I/O 超时 5 分钟 |
| `--contimeout=60` | 连接超时 1 分钟 |

### 4.2 为什么不用 `--checksum`

❌ **rsync 到 115 绝对不加 `--checksum`。**

`--checksum` 会让 rsync 读取**远端每个文件的内容**算哈希。CloudDrive2 是 FUSE 挂载，
读远端内容要经过网络往返 —— 对于几千个文件的目录，这一步会慢到不可接受（数量级差距）。

**替代方案**：rsync 默认的「文件名 + 大小 + mtime」三元组判断已经足够。
照片是**一次写入、永不修改**的数据，大小相同且 mtime 相同基本等价于内容相同。

**如果确实需要内容级校验**：用 `modules/export/verify_backup.py` ——
它通过 `os.stat()` 比较远端文件**大小**（不下载内容），比 SHA256 快 100 倍以上，
作为独立的验证步骤运行，而不是塞进 rsync 的每次同步里。

### 4.3 NAS 冷备频率

| NAS HDD 转速 | 启动时间 | 100GB 传输 (千兆网) | 总耗时 |
|-------------|---------|-------------------|--------|
| 5400 RPM | ~2 分钟 | ~15 分钟 | ~20 分钟 |
| 7200 RPM | ~2 分钟 | ~15 分钟 | ~20 分钟 |

NAS 冷备按 **每月 1 次**（第一周周一）执行，不随每次导出触发。

**为什么不是每次**：冷备份的意义是「不可变副本」，价值在于**时间点分散**而非频率高。
每周唤醒 NAS 反而增加硬盘启停磨损 —— 对 HDD 来说，启停是最伤寿命的工况之一。

### 4.4 Live Photo 处理

osxphotos 的 `live_photo=True` 参数确保：

- `IMG_1234.HEIC`（静态帧）和 `IMG_1234.MOV`（动态部分）**成对导出**
- 两个文件落在同一目录，文件名关联清晰
- 在 Finder / 网盘中可以分别查看

**代价**：Live Photo 会占用两份空间（HEIC + MOV）。
如果空间紧张，可以在 `config.env` 里关掉 —— 但那样动态部分就永久丢失了。

### 4.5 iCloud 空间释放：为什么是手动的

**这不是没做完，而是被 macOS 封锁了。**

macOS Sequoia 的 Photos 10.0 封锁了 AppleScript / JXA 的 `delete` 命令，
任何脚本化的照片删除都会被系统拒绝。实测多种方式均无效 ——
Apple 在相册数据的删除权限上做了硬性保护。

所以 Step 2 **只生成报告**：按年/月列出「已备份且可以删除」的照片数量，
用户收到邮件后自己去 Photos.app 里手动删。

> 手动作业时**从最老的年份开始删**，删完等 iCloud 同步完成再删下一批 ——
> 一次删太多容易触发 iCloud 同步排队，且中途出错不好回溯。

---

## 5. 配置

只需改 `config/config.env`（gitignored，模板见 `config/config.env.example`）：

| 变量 | 说明 |
|------|------|
| `BACKUP_ROOT` | 本地中转根目录 |
| `CLOUDDRIVE_MOUNT` | CloudDrive2 挂载点 |
| `REMOTE_115_DIR` | 115 上的归档目录 |
| `STAGING_RETENTION_MONTHS` | 本地中转保留月数（默认 3） |
| `NAS_*` | NAS 冷备相关（IP / MAC / 用户 / 路径 / 开关） |
| `SMTP_*` | 邮件通知 |
| `DEVICE_MODELS_FILE` | 设备型号映射表路径 |

**注意**：脚本会 `source config/config.env`，环境变量优先级高于脚本内的默认值。
不要在脚本里写死路径 —— 改了配置却不见效，通常是这个原因。

---

## 6. 故障排查

### 6.1 常见问题

| 症状 | 可能原因 | 解决 |
|------|---------|------|
| osxphotos 报权限错误 | TCC：`python3.11` 没有完全磁盘访问权限 | 系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 添加 `/opt/homebrew/bin/python3.11` |
| CloudDrive2 挂载点消失 | CloudDrive2 崩溃 / 登出 | 跑 `modules/utils/clouddrive_watchdog.sh`，**不要直接 kill clouddrive 进程** |
| NAS WOL 无响应 | MAC 地址错误 / 网络隔离 | 检查 MAC 地址，确认 Mac 与 NAS 同网段（注意 `NAS_IP` 默认值是占位符） |
| rsync 到 NAS 极慢 | SMB 协议在大文件场景下效率低 | 考虑 rsync daemon 模式或 NFS |
| Step 0 报 FATAL | 有老文件在 115 上缺失 | **先查 115，不要绕过 Step 0** —— 它拦下来是为了防止误删 |
| launchd 任务不执行 | plist 语法 / 路径错误 | `plutil -lint <plist>` 检查语法 |
| 手动跑 osxphotos 报权限错但 launchd 正常 | 手动 bash 的 TCC 权限链断裂 | 这是**环境问题不是代码问题**，以 launchd 运行为准 |

### 6.2 日志位置

```
~/PhotoBackup/.logs/
├── pipeline.log              # 主流水线日志
├── export.log                # 导出详细日志
├── backup_115.log            # 115 备份日志
├── launchd_stdout.log        # launchd 捕获的 stdout
├── launchd_stderr.log        # launchd 捕获的 stderr
├── launchd_watchdog_*.log    # 看门狗日志
└── reports/                  # 每轮生成的邮件报告
    └── report-YYYYMMDD-HHMMSS.txt
```

### 6.3 手动执行

```bash
cd /Users/your_user/Workspace/Projects/O-Data-Center-System

# 预览导出（不实际写文件）
python3.11 modules/export/photo_export.py --dry-run

# 只看归档验证结果
python3.11 modules/export/verify_backup.py --target 115

# 测试 NAS 唤醒
bash modules/backup/nas/backup_to_nas.sh --wol-only

# 完整流水线
bash modules/scheduler/photo_backup_weekly.sh

# 看最新报告
ls -t ~/PhotoBackup/.logs/reports/ | head -1 | xargs -I{} cat ~/PhotoBackup/.logs/reports/{}
```

> ⚠️ 手动执行请用**绝对路径**调用 `python3.11`。
> 相对路径下的 python 可能缺少完全磁盘访问权限，导致 osxphotos 报错。

---

## 附录 A: CloudDrive2 与 rclone 方案对比

| 维度 | CloudDrive2 + rsync | rclone |
|------|-------------------|--------|
| 开源 | ❌ 闭源，免费增值（免费版 3 网盘位） | ✅ 开源 (MIT) |
| 115 支持 | 官方支持，维护活跃 | 原生 backend，内置公共 client_id |
| 配置难度 | GUI 登录即可 | 需 `rclone config` 走一次扫码授权 |
| 传输方式 | 挂载为本地盘，rsync 操作 | `rclone mount` 或 `rclone sync` |
| 断点续传 | rsync `--partial` | rclone 原生支持 |
| 资源占用 | 常驻 ~200MB | 按需启动 |
| 稳定性 | 挂载点偶有断开，需看门狗重连 | 较稳定 |
| 成本 | 部分功能需会员 | 免费 |

**当前选择**：CloudDrive2。GUI 配置省事，挂载点模式让 rsync 逻辑零改动。

**迁移路径**（如果日后想摆脱会员限制）：rclone 的 115 backend **内置公共 client_id**，
扫码授权即可，**不需要自己入驻 115 开放平台、不需要 ICP 备案**（自用属于非公开性服务）。

迁移成本极低：只改 `config.env` 里的 `CLOUDDRIVE_MOUNT` 一行。
建议先在 `/Volumes/rclone115` 旁路挂载跑 `--dry-run` 对比，确认一致后再切。

## 附录 B: 存储空间估算

| 项目 | 预估大小 |
|------|---------|
| 3 个月照片 (假设每天 20 张 HEIC) | ~20 GB |
| 3 个月视频 (假设每天 2 分钟 4K) | ~30 GB |
| 备份文件夹总计 | ~50 GB |
| Mac Photos 库（全量历史） | ~200-500 GB |
| 115 网盘备份（累计增量） | 取决于网盘容量 |
| NAS 备份 | 取决于 NAS HDD 容量 |

> 建议：Mac Mini 本地留出 **2× 照片库大小 + 100GB** 余量。
> 115 网盘建议至少 1TB，NAS HDD 建议 4TB+。

---

*文档版本: v1.1 | 更新日期: 2026-09-13*
