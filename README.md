# iCloud Photo Archiver

> iPhone 照片视频 → iCloud → Mac Mini 自动分类 → 异地双备份 → 空间回收，全链路无人值守。

拍了照片就忘掉它——你的 Mac Mini 会帮你整理、备份到 115 网盘和群晖 NAS，然后清理本地中转空间。

**设计要点**：Shell 只做编排，所有判断逻辑都在独立可测的 Python 模块里；备份目标是配置驱动的，
增删一个备份目的地不需要改代码。

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
icloud-photo-archiver/
│
├── modules/                            # 🧩 完整的 Python 包，以 python -m 调用
│   ├── export/
│   │   ├── photo_export.py             #   核心：导出 + 五级分类
│   │   └── verify_backup.py            #   归档完整性验证（清理前的安全闸）
│   ├── classify/                       #   🆕 分类引擎：判定素材去哪（不依赖 Photos 库）
│   │   ├── metadata.py                 #     读文件本身：sips(图) + ffprobe(视频)
│   │   ├── device.py                   #     机型 → 归档文件夹名
│   │   ├── planner.py                  #     五级路径构建 + 冲突处理
│   │   ├── scan.py                     #     CLI：扫描目录 → 分类计划（只读）
│   │   └── apply.py                    #     CLI：按计划复制（源文件只读）
│   ├── cleanup/
│   │   ├── clean_staging.py            #   本地中转清理（带备份确认安全阀）
│   │   └── icloud_report.py            #   iCloud/Photos 可清理报告
│   ├── preflight/
│   │   └── check_photos_access.py      #   Photos 库访问权限（TCC）检查
│   ├── backup/
│   │   ├── 115/backup_to_115.sh        #   rsync → 115（异地热备）
│   │   └── nas/backup_to_nas.sh        #   WOL + rsync → NAS（本地冷备）
│   ├── scheduler/
│   │   ├── photo_backup_weekly.sh      #   周度主流水线（纯编排，零内联 Python）
│   │   └── launchd/                    #   调度配置（2 个 job）
│   └── utils/                          #   共享地基
│       ├── config.py                   #   配置读取（Python 侧）
│       ├── manifest.py                 #   备份清单：目标标记 / 风险文件 / 幽灵条目
│       ├── email_notify.py             #   SMTP 报告邮件
│       ├── logger.sh · notify.sh       #   日志与 macOS 通知
│       └── clouddrive_watchdog.sh      #   CloudDrive2 挂载健康自愈
│
├── config/
│   ├── load.sh                         #   配置装载（shell 侧唯一入口）
│   ├── config.env.example              #   公用配置模板（路径、阈值、开关）
│   ├── secrets.env.example             #   保密配置模板（邮箱、内网地址、SSH）
│   ├── device-models.json.example      #   设备型号映射模板
│   └── device-models.json              #   实际使用的那份（含你家设备清单，gitignored）
│
├── web/                                # 🆕 图形界面（删掉它，命令行系统照常工作）
│   ├── backend/                        #   FastAPI：任务 / SSE / 计划分页
│   ├── frontend/                       #   Svelte 5 + Vite
│   ├── launchd/                        #   常驻自启模板（绑 127.0.0.1）
│   └── deploy.sh · dev.sh              #   构建 / 安装 / 卸载 / 开发
│
├── steps/                              # 📚 分步操作手册（step-00 ~ step-06）
└── .state/ · .logs/                    # 运行状态与日志（gitignored）
```

**配置优先级**：环境变量 > `config.env` > `secrets.env` > 代码默认值。
因此换目录做实验不必改文件：`BACKUP_ROOT=/tmp/x python -m modules.export.photo_export --dry-run`。

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
iPhone_15_Pro/2026/06/personal/photos/      ← A 手机拍的照片
Pixel_8/2026/06/personal/photos/            ← B 手机拍的照片
iPhone_13/2026/06/family-shared/videos/     ← 共享相簿里的视频
Unknown_Device/2026/06/personal/photos/     ← 无法识别设备
```

分类依据是 **EXIF 相机型号**，映射表在 `config/device-models.json` —— 不限于 Apple 设备，
任何相机型号都能登记归类，未登记的按型号原样归档。

**115 网盘视角**：一台设备一个顶层文件夹，可直接按人分享：

```
/Volumes/CloudDrive/PhotoBackup/
├── iPhone_15_Pro/          ← 分享给 A
├── Pixel_8/                ← 分享给 B
└── iPhone_13/              ← 家庭共享
```

---

## 文件夹分类器 + 图形界面 🆕

上面那套五级规则，现在也能用在 **Photos 库之外的老素材**上 —— 多年换机累积、
散落在普通文件夹里的照片视频。

```bash
# 命令行
export PYTHONPATH="$PWD"
python -m modules.classify.scan  --input ~/Pictures/old --output ~/Classified --plan-out /tmp/plan.json
python -m modules.classify.apply --plan /tmp/plan.json

# 图形界面
web/deploy.sh --run            # 然后浏览器开 http://127.0.0.1:8787
```

两条路走的是**同一套规则** —— `modules/classify/` 是唯一的真相源，
网页后端只是起子进程调 CLI 并解析 `[RESULT]` 行。

**取与判分离**：`export/` 负责从 Photos.app **取**素材（依赖 osxphotos），
`classify/` 只负责判定素材**去哪**（零依赖，只看文件本身）。因此两批素材能共用规则。

> ⚠️ 分类是**手动触发**的两步操作：先扫描出计划供审阅，确认后才复制。
> **源目录全程只读** —— 只往你指定的输出目录复制，不移动、不改名、不删除。
> 网页界面里没有、也不会有任何删除入口。

细节见 [`modules/classify/README.md`](modules/classify/README.md) 与 [`web/README.md`](web/README.md)。

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
# 配置：三份文件，代码里不硬编码任何环境相关值
cp config/config.env.example config/config.env                 # 公用：路径、阈值、开关
cp config/secrets.env.example config/secrets.env               # 保密：邮箱、内网地址、SSH
cp config/device-models.json.example config/device-models.json # 设备：EXIF 型号 → 机主

# 后两份 + device-models.json 都已被 .gitignore 忽略 —— 它们记录的是你的个人环境

# 依赖：分类引擎零依赖，requirements.txt 只有 osxphotos（导出用）
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 分模块运行 —— modules/ 是包，PYTHONPATH 指向项目根
export PYTHONPATH="$PWD"
python -m modules.export.photo_export --dry-run       # 预览导出
python -m modules.cleanup.clean_staging --dry-run     # 预览清理
python -m modules.export.verify_backup                # 验证归档完整性
python -m modules.utils.manifest show                 # 查看备份清单

# Shell 模块同样可单独跑
bash modules/backup/115/backup_to_115.sh --dry-run
bash modules/backup/nas/backup_to_nas.sh --wol-only   # 仅测试 WOL 唤醒

# 完整流水线
bash modules/scheduler/photo_backup_weekly.sh         # 正式执行
bash modules/scheduler/photo_backup_weekly.sh --test  # 测试模式
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

## 模块化设计

三条约束贯穿整个代码库。

**1. Shell 只做编排，判断逻辑全在 Python 模块里。**

主干 `photo_backup_weekly.sh` 里没有一行内联 Python。早期版本用 heredoc 把 Python 塞进 shell，
既无法单独测试也无法复用；现在它只负责按顺序调用模块、收集输出、决定后续步骤走不走。

**2. 模块之间用机器可读的结果行通信。**

每个模块在输出末尾打印一行 `[RESULT] k=v k=v`，编排层用取值函数提取，
而不是靠脆弱的 grep 去抓中文日志。这样模块的日志措辞可以随便改，契约不变。

| 模块 | 结果行 |
|------|--------|
| `photo_export` | `[RESULT] exported=N skipped=N failed=N size_bytes=N` |
| `clean_staging` | `[RESULT] cleaned=N kept=N failed=N` |
| `verify_backup` | `[RESULT] verified=N passed=N failed=N blocked=bool` |

**3. 备份目标是配置驱动的。**

`BACKUP_TARGETS="115,nas"` 决定备份到哪几个目的地，每个目的地从 `REMOTE_<目标名大写>_DIR` 读路径；
清单里的标记字段也按目标生成（`backed_up_115`、`backed_up_nas`）。
**新增一个备份目的地 = 改配置，不用改任何 Python 代码。**

**独立性**：每个模块都能单独运行、调试、替换。例如将来改用 rclone 挂载 115，
只需替换 `modules/backup/115/backup_to_115.sh`，其他模块不受影响。

---

## 安全设计

这一节是本项目最花心思的地方——**清理功能一旦判错就是永久丢照片**。

- **清理前先验证**：Step 0 用 `os.stat` 逐一比对即将被清理的老文件在远端的大小，
  缺一个就 FATAL，阻塞整条流水线的删除动作。保留期内的文件不验证（本次 rsync 会传上去）。
- **安全阀**：只有当一个月份目录下**每一个文件**都确认已备份，才允许清理它。
  宁可留一堆没用的本地文件，也不能删掉唯一一份还没上云的照片。
- **绝不用硬删除兜底**：默认移入废纸篓；移动失败就保留原目录并计入 `failed`，不退化成 `rm -rf`。
- **失败不冒充成功**：rsync 失败就不打「已备份」标记——否则等于给清理逻辑发了假通行证。
- **空配置即中止**：`CLEANUP_REQUIRE_TARGETS` 为空时直接拒绝执行，
  因为「没有任何目标需要确认」等价于「无条件删除」。

---

## 技术栈

- **照片导出**: [osxphotos](https://github.com/RhetTbull/osxphotos) (Python)
- **差异同步**: rsync (macOS 内置)
- **115 挂载**: CloudDrive2
- **NAS 冷备**: wakeonlan + rsync + SSH
- **调度**: macOS launchd
- **通知**: osascript (原生通知中心) + SMTP 邮件
- **元数据读取**: `sips` + `ffprobe` (macOS / Homebrew，**零新增 Python 依赖**)
- **图形界面**: FastAPI + uvicorn + Svelte 5 + Vite

---

## 许可

[MIT](./LICENSE)
