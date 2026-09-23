# Step 6: 全自动调度

> 使用 macOS launchd 实现定时自动化执行，整个备份系统无需人工干预。

---

## 6.1 调度方案选择

| 方案 | 优点 | 缺点 | 推荐度 |
|------|------|------|--------|
| **launchd** | macOS 原生，开机自启，资源低 | XML 配置稍繁琐 | ⭐⭐⭐⭐⭐ |
| cron | 配置简单 | macOS 已弃用，不保证运行 | ❌ |
| Hammerspoon | Lua 脚本灵活 | 需额外安装，GUI 依赖 | ⭐⭐ |
| 自定义 daemon | 完全控制 | 开发成本高 | ⭐ |

**选择 launchd 的理由**：
- macOS 系统级调度器，苹果官方维护
- 开机自启、崩溃自动重启
- 日志自动集成到 unified logging
- 可以按日历间隔、时间间隔、文件变更等多种方式触发

---

## 6.2 调度架构

```
launchd
  │
  ├── com.datacenter.clouddrive-watchdog.plist
  │     │  开机时 RunAtLoad 拉起一次
  │     └──→ clouddrive_watchdog.sh   (保活 CloudDrive2 挂载点)
  │
  └── com.datacenter.photo-backup-weekly.plist
        │  每周一 + 周四 凌晨 3:00 触发
        │
        └──→ photo_backup_weekly.sh (主流水线)
               ├── Step 0  verify_backup.py      (归档验证, FATAL 门)
               ├── Step 1  本地中转清理
               ├── Step 2  iCloud 可清理报告
               ├── Step 3  photo_export.py       (导出+五级分类)
               ├── Step 4  backup_to_115.sh      (115 备份)
               ├── Step 5  backup_to_nas.sh      (每月第一周周一)
               └── Step 6  邮件报告 + 通知
```

> **只有一个定时任务。** 清理不再是独立任务 —— 它作为 Step 0/1 内嵌在主流水线里，
> 因为「先验证、再清理、后备份」必须是一个原子序列，拆成两个 job 会失去顺序保证。

---

## 6.3 launchd plist 配置

### 6.3.1 周度备份任务

文件位置：`modules/scheduler/launchd/com.datacenter.photo-backup-weekly.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.datacenter.photo-backup-weekly</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <!-- ⚠️ 部署前将 your_user 替换为你的实际用户名 -->
        <string>/Users/your_user/Workspace/Projects/O-Data-Center-System/modules/scheduler/photo_backup_weekly.sh</string>
    </array>

    <!-- 每周一 + 周四凌晨 3:00 执行 -->
    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Weekday</key>
            <integer>1</integer>
            <key>Hour</key>
            <integer>3</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
        <dict>
            <key>Weekday</key>
            <integer>4</integer>
            <key>Hour</key>
            <integer>3</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
    </array>

    <!-- 环境变量 -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>BACKUP_ROOT</key>
        <string>/Users/your_user/PhotoBackup</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin</string>
        <key>HOME</key>
        <!-- ⚠️ 部署前替换为你的 home 目录 -->
        <string>/Users/your_user</string>
    </dict>

    <!-- 日志输出 -->
    <key>StandardOutPath</key>
    <string>/Users/your_user/PhotoBackup/.logs/launchd_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/your_user/PhotoBackup/.logs/launchd_stderr.log</string>

    <!-- 仅在日历触发时运行，不在加载时运行 -->
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

**`StartCalendarInterval` 用数组而非字典**：数组里每个 `<dict>` 是一个独立的触发时刻。
写成一个字典里塞两个 `Weekday` 键是不生效的（XML 字典键必须唯一）——
两个时刻必须并列成数组的两个元素。

**`Weekday` 取值**：`1`=周一，`2`=周二，…，`7`=周日（launchd 用 1-7 表示周一到周日，**不是** 0-6）。

---

## 6.4 部署 launchd 任务

### 6.4.1 配置路径替换

部署前，将 plist 文件中的占位符替换为实际路径：

```bash
# plist 里的路径已按本机实际填写；换机器时替换用户名
sed -i '' 's/your_user/YOUR_ACTUAL_USERNAME/g' \
    modules/scheduler/launchd/*.plist
```

### 6.4.2 加载任务

```bash
# 复制全部 plist 到 launchd 目录
cp modules/scheduler/launchd/*.plist ~/Library/LaunchAgents/

# 验证 plist 语法
plutil -lint ~/Library/LaunchAgents/com.datacenter.*.plist

# 加载（立即生效）
for j in ~/Library/LaunchAgents/com.datacenter.*.plist; do
    launchctl load "$j"
done
```

### 6.4.3 管理任务

```bash
# 查看任务状态
launchctl list | grep com.datacenter

# 手动触发一次（测试用）
launchctl start com.datacenter.photo-backup-weekly

# 停止任务
launchctl stop com.datacenter.photo-backup-weekly

# 修改后重新加载
launchctl unload ~/Library/LaunchAgents/com.datacenter.photo-backup-weekly.plist
launchctl load ~/Library/LaunchAgents/com.datacenter.photo-backup-weekly.plist

# 完全移除
launchctl unload ~/Library/LaunchAgents/com.datacenter.photo-backup-weekly.plist
rm ~/Library/LaunchAgents/com.datacenter.photo-backup-weekly.plist
```

---

## 6.5 主流水线脚本

脚本位置：`modules/scheduler/photo_backup_weekly.sh` —— **以脚本本身为准**，本节只讲两个易错的门控逻辑。

### 6.5.1 NAS 月度判断

NAS 冷备不该每周跑。判断逻辑是**周数取模 + 限定周一**两个条件的与：

```bash
current_week=$(date +%W)
current_week=$((10#$current_week))          # 去前导零，否则被当八进制
if [ $((current_week % interval)) -eq 0 ] && [ "$(date +%u)" = "1" ]; then
    do_nas_backup=true
fi
```

两个细节：

- **`10#$current_week`**：`date +%W` 会产出 `08`、`09` 这类带前导零的字符串，
  bash 算术运算会当成八进制而报错，必须先转十进制
- **`[ "$(date +%u)" = "1" ]`**：流水线一周跑两次（周一和周四），
  只加周数判断的话，冷备周里**周一周四会各跑一次**。加上「必须是周一」才是真正的每月一次

### 6.5.2 NAS 未配置时跳过

`config.env` 里 `NAS_IP` / `NAS_MAC` 还是占位符时，Step 5 直接跳过而不是报错：

```bash
if [ "${NAS_IP:-192.168.1.100}" = "192.168.1.100" ] || \
   [ "${NAS_MAC:-00:11:32:AA:BB:CC}" = "00:11:32:AA:BB:CC" ]; then
    log "跳过 NAS 备份 (NAS 尚未配置)"
fi
```

这样「还没买 NAS」和「NAS 坏了」是两种不同的日志，排查时不会混淆。

---

## 6.6 macOS 通知集成

### 6.6.1 通知脚本

脚本位置：`modules/utils/notify.sh`

```bash
#!/bin/bash
# macOS 原生通知

send_notification() {
    local title="$1"
    local message="$2"
    local sound="${3:-default}"  # default, glass, (空=静默)

    osascript -e "
        display notification \"$message\" \
        with title \"$title\" \
        sound name \"$sound\"
    " 2>/dev/null || true
}

# 使用示例
send_notification "📸 照片备份" "本周备份流水线完成 ✅"
send_notification "⚠️ 照片备份" "115 备份失败，请检查日志" "Glass"
```

### 6.6.2 通知触发条件

| 触发条件 | 标题 | 内容 | 声音 |
|---------|------|------|------|
| 流水线全部完成 | 📸 照片备份 | 全部完成 ✅ | 静默 |
| 导出失败 | ⚠️ 照片备份 | 导出失败 | Glass |
| 115 备份失败 | ⚠️ 照片备份 | 115 备份失败 | Glass |
| NAS 备份失败 | ⚠️ 照片备份 | NAS 备份失败 | Glass |
| 归档验证失败 (Step 0) | 🛑 照片备份 | 流水线已中止 | Glass |

> 为了避免凌晨被通知声吵醒，凌晨备份的通知默认使用静默模式。

---

## 6.7 运行时间线

```
03:00:00  Step 0  归档验证            ██░░░░░░░░  (~1-3 min)
03:03:00  Step 1  本地中转清理        █░░░░░░░░░  (~1 min)
03:05:00  Step 2  iCloud 可清理报告   █░░░░░░░░░  (~2 min)
03:07:00  Step 3  导出 + 分类         ████░░░░░░  (~5-10 min)
03:17:00  Step 4  115 备份            ████████░░  (~15-30 min, 取决于增量)
03:47:00  Step 5  NAS 备份 (仅冷备周)  ██████████  (~20 min)
04:07:00  Step 6  邮件报告 + 通知
```

总耗时：**非冷备周 ~25-45 分钟；冷备周 ~45-65 分钟。**

> 冷备周 = 周数能被 `NAS_BACKUP_WEEKS_INTERVAL`（默认 4）整除的那一周的**周一**。
> 周四那一轮永远不跑 NAS。

---

## 6.8 验证与监控

### 6.8.1 检查调度是否正常

```bash
# 查看 launchd 任务
launchctl list | grep com.datacenter

# 查看最后执行时间
ls -la ~/PhotoBackup/.logs/pipeline.log

# 查看执行日志
tail -20 ~/PhotoBackup/.logs/pipeline.log
```

### 6.8.2 日志轮转

```bash
# 保留最近 30 天的日志
# 可在流水线脚本末尾添加:
find ~/PhotoBackup/.logs/ -name "*.log" -mtime +30 -delete
```

### 6.8.3 健康检查脚本

```bash
#!/bin/bash
# 检查备份系统是否健康运行

ERRORS=0

# 检查最后备份时间
LAST_BACKUP=$(cat ~/PhotoBackup/.state/last_export.json 2>/dev/null | python3.11 -c "import sys,json; print(json.load(sys.stdin).get('last_export_ts','never'))")
echo "上次导出: $LAST_BACKUP"

# 检查 CloudDrive2 挂载
if mount | grep -q CloudDrive; then
    echo "✅ CloudDrive2 在线"
else
    echo "❌ CloudDrive2 离线"
    ERRORS=$((ERRORS + 1))
fi

# 检查磁盘空间
DISK_USAGE=$(df -h ~/PhotoBackup | tail -1 | awk '{print $5}' | tr -d '%')
echo "备份磁盘使用: ${DISK_USAGE}%"
if [ "$DISK_USAGE" -gt 90 ]; then
    echo "⚠️  磁盘空间紧张"
    ERRORS=$((ERRORS + 1))
fi

if [ $ERRORS -eq 0 ]; then
    echo "✅ 系统健康"
else
    echo "⚠️  发现 $ERRORS 个问题"
fi
```

---

## 6.9 常见问题

### Q: launchd 任务不执行？

```bash
# 1. 检查 plist 语法
plutil -lint ~/Library/LaunchAgents/com.datacenter.photo-backup-weekly.plist

# 2. 检查路径是否正确
# plist 中的所有路径必须是绝对路径

# 3. 检查权限
ls -la ~/Library/LaunchAgents/

# 4. 检查任务是否加载
launchctl list | grep com.datacenter
# 如果 PID 列为空 → 任务存在但未运行
# 如果 status 列不为空 → 上次执行的退出码

# 5. 手动启动测试
launchctl start com.datacenter.photo-backup-weekly

# 6. 查看系统日志
log show --predicate 'subsystem == "com.apple.launchd"' --last 1h | grep datacenter
```

### Q: 错过了执行窗口怎么办？

如果 Mac 在周一/周四凌晨 3:00 处于关机/睡眠状态，launchd 会在 Mac 下次开机后尽快补执行。但实际上：

- 如果 Mac 一直休眠，等到上午才唤醒 → 任务在唤醒后立即执行
- 如果 Mac 错过了整轮 → 任务等到下一个周一或周四才执行
- **不会漏数据**：导出是增量的，基线来自磁盘扫描而不是「上次跑了没有」，
  所以少跑一轮只是晚几天备份，不丢文件

> 这也是为什么一周跑两次（周一 + 周四）：单次错过最多等 3 天，
> 而不是整周。冗余的调度比精确的调度更重要。

---

*上一步 ← [Step 4: 备份至群晖 NAS](../step-04-backup-nas/README.md)*
