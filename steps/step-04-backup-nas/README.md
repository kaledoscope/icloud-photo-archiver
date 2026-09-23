# Step 4: 备份至群晖 NAS (冷备份)

> 定期唤醒 NAS → 挂载共享 → rsync 差异同步 → 卸载 → 关机。冷备份的意义是「不可变副本」，离线状态天然隔离勒索软件和误删除。

---

## 4.1 什么是冷备份

```
热备份 (Hot)              温备份 (Warm)              冷备份 (Cold)
─────────────────────────────────────────────────────────────
24x7 在线                 定期在线                   平时离线
随时读写                  按规定计划                  按需启动
115 网盘                   -                         群晖 NAS (本步骤)
功耗: 持续                功耗: 间歇                  功耗: 仅备份时
风险: 在线攻击面          风险: 中等                  风险: 最小
```

**冷备份的核心价值**：
- NAS 平时处于**关机状态**，物理上不可访问
- 即使 Mac 被勒索软件加密，攻击者无法触达关机的 NAS
- 即使云盘账号被盗，NAS 上的副本依然安全
- "不可变副本"——每月一次的快照，误删可以回溯

---

## 4.2 前置条件

确认以下内容（Step 0 已完成）：

```bash
# 1. WOL 可用
which wakeonlan

# 2. SSH 免密登录
ssh synology-nas "echo OK"

# 3. NAS MAC 地址已知
# 例如: 00:11:32:AA:BB:CC

# 4. SMB 挂载测试通过
mount -t smbfs "//user@NAS_IP/photo_backup" /Volumes/NAS_Backup
ls /Volumes/NAS_Backup/
diskutil unmount /Volumes/NAS_Backup
```

---

## 4.3 完整冷备份流程

```
Step 1: WOL 唤醒
  │ wakeonlan $NAS_MAC
  │ 等待 ping 通 (最多5分钟)
  │ 最多重试 3 次
  ▼
Step 2: 等待 SSH 就绪
  │ 轮询 SSH 连接 (每10秒)
  │ 最多等待 5 分钟
  ▼
Step 3: 挂载 NAS 共享
  │ mount -t smbfs
  │ 创建远端备份目录
  ▼
Step 4: rsync 差异同步
  │ 与本地备份目录镜像同步
  │ --checksum 校验
  ▼
Step 5: 卸载 NAS 共享
  │ diskutil unmount
  │ 等待缓冲区刷盘
  ▼
Step 6: 远程关机
  │ ssh synology-nas "sudo synopoweroff"
  │ 等待 NAS 完全关闭
  ▼
完成
```

### 4.3.1 为什么流程这么长

每一步都有**超时和重试**机制。NAS 从通电到 SSH 就绪需要：
- 系统启动 (~1 分钟)
- DSM 服务启动 (~1 分钟)
- SSH 守护进程就绪 (~30 秒)

在机械硬盘的 NAS 上，冷启动可能需要 3-5 分钟。

---

## 4.4 各步骤详解

### 4.4.1 WOL 唤醒

```bash
# WOL 原理：向目标 MAC 地址所在网段广播「魔术包」
# 魔术包 = 6字节 0xFF + MAC地址重复16次
wakeonlan 00:11:32:AA:BB:CC

# WOL 的前提条件：
# 1. NAS 和 Mac 在同一局域网（或 VLAN 互通）
# 2. NAS BIOS/UEFI 中已启用 WOL
# 3. NAS 网卡支持 WOL
# 4. NAS 处于 S5 (软关机) 状态，非 S4 (休眠) 或断电
```

**重试机制**：

```
第1次 WOL → 等 10秒 → ping
  ├── ping 通 → 继续等待 SSH
  └── ping 不通 → 第2次 WOL → 等5分钟 → ping
                    ├── ping 通 → 继续等待 SSH
                    └── ping 不通 → 第3次 WOL → 等5分钟 → ping
                          ├── ping 通 → 继续等待 SSH
                          └── ping 不通 → ❌ 失败，退出
```

### 4.4.2 等待 SSH 就绪

```bash
# 轮询检查 SSH，最多等待 5 分钟
MAX_WAIT=300  # 秒
INTERVAL=10   # 每10秒检查一次

waited=0
while [ $waited -lt $MAX_WAIT ]; do
    if ssh -o ConnectTimeout=5 -o BatchMode=yes synology-nas "echo OK" 2>/dev/null; then
        echo "NAS SSH 就绪 (等待了 ${waited}s)"
        break
    fi
    sleep $INTERVAL
    waited=$((waited + INTERVAL))
done
```

### 4.4.3 挂载 NAS 共享

```bash
# SMB 挂载
mount -t smbfs "//user@192.168.1.100/photo_backup" /Volumes/NAS_Backup

# SMB vs NFS vs AFP 选择：
# - SMB：macOS 原生支持最好，推荐
# - NFS：大文件传输效率更高，但 macOS 配置复杂
# - AFP：已弃用，不推荐
```

### 4.4.4 rsync 同步

参数与 115 备份保持一致的策略（详见 Step 3）：

```bash
rsync -avhP \
    --checksum \
    --partial \
    --delete \
    --exclude '.state/' \
    --exclude '.logs/' \
    --exclude '.DS_Store' \
    --exclude '._*' \
    --timeout=300 \
    --stats \
    "$BACKUP_ROOT/" \
    "$NAS_REMOTE_DIR/"
```

### 4.4.5 卸载

```bash
# 正常卸载
diskutil unmount /Volumes/NAS_Backup

# 如果正常卸载失败（有进程占用），强制卸载
diskutil unmount force /Volumes/NAS_Backup

# 等待 5 秒让缓冲区刷盘（重要！）
sleep 5
```

> ⚠️ **重要**：卸载前必须确保 rsync 已完全退出。如果强制卸载时 rsync 还在写数据，可能造成远端文件损坏。

### 4.4.6 远程关机

```bash
# 方式 1：群晖专用命令（推荐）
ssh synology-nas "sudo synopoweroff"

# 方式 2：标准 Linux 关机
ssh synology-nas "sudo poweroff"

# 方式 3：DSM API（不需要 SSH sudo）
curl "http://192.168.1.100:5000/webapi/entry.cgi?api=SYNO.Core.Shutdown&version=1&method=shutdown"
```

---

## 4.5 备份脚本

脚本位置：`modules/backup/nas/backup_to_nas.sh`

### 4.5.1 使用方式

```bash
# 直接执行（使用默认配置）
bash modules/backup/nas/backup_to_nas.sh

# 使用自定义配置
source config/config.env
bash modules/backup/nas/backup_to_nas.sh

# 手动测试各子步骤
# 仅测试 WOL:
bash modules/backup/nas/backup_to_nas.sh --wol-only

# 挂载后不关机（调试用）:
bash modules/backup/nas/backup_to_nas.sh --no-shutdown
```

### 4.5.2 关键配置

```bash
# config/config.env 中的 NAS 相关配置
NAS_IP="192.168.1.100"           # NAS IP 地址
NAS_MAC="00:11:32:AA:BB:CC"      # NAS MAC 地址
NAS_USER="admin"                 # NAS 用户名
NAS_SHARE="photo_backup"         # 共享文件夹名称
NAS_MOUNT_POINT="/Volumes/NAS_Backup"  # 本地挂载点
```

### 4.5.3 输出示例

```
[2026-06-20 03:25:30] ===== NAS 冷备份开始 =====
[2026-06-20 03:25:30] NAS 离线，开始 WOL 唤醒...
[2026-06-20 03:25:30] 发送 WOL 唤醒包 → 00:11:32:AA:BB:CC
[2026-06-20 03:26:15] NAS 已响应 ping，继续等待 SSH 就绪...
[2026-06-20 03:28:35] NAS SSH 就绪 (等待了 120s)
[2026-06-20 03:28:37] 挂载 NAS 共享: photo_backup
[2026-06-20 03:28:40] NAS 挂载成功 → /Volumes/NAS_Backup
sending incremental file list...
...
[2026-06-20 03:45:12] ✅ NAS 备份完成
[2026-06-20 03:45:17] 卸载 NAS 共享...
[2026-06-20 03:45:20] 发送 NAS 关机指令...
[2026-06-20 03:45:22] ✅ NAS 已关机
[2026-06-20 03:45:22] ===== NAS 冷备份成功 (总耗时 19分52秒) =====
```

---

## 4.6 冷备份频率建议

| 频率 | 适用场景 | 优缺点 |
|------|---------|--------|
| 每周 | 数据变化大，安全意识极强 | NAS 频繁启停，HDD 磨损增加 |
| **每 4 周（推荐）** | 一般家庭使用 | 平衡数据安全与硬件寿命 |
| 每 8 周 | 数据变化小 | 间隔过长，增量累积大 |

> 冷备份的默认频率：**每月一次**（在流水线中通过周数取模判断，`week % 4 == 0`）。

### 4.6.1 时间估算

| 项目 | 时间 |
|------|------|
| WOL + NAS 启动 | ~3 分钟 |
| SMB 挂载 | ~30 秒 |
| 增量 rsync (每月增量 ~20GB) | ~15-20 分钟 (千兆网) |
| 卸载 + 关机 | ~30 秒 |
| **总计** | **~20-25 分钟** |

---

## 4.7 故障处理

### 4.7.1 WOL 失败

| 症状 | 原因 | 解决 |
|------|------|------|
| 3次 WOL 均无响应 | NAS 不在同一子网 | 检查路由器 VLAN 设置 |
| | NAS 网卡不支持 WOL | 在 DSM 中确认 WOL 已开启 |
| | MAC 地址错误 | 在 DSM 信息中心重新确认 |
| | NAS 完全断电 | 检查电源，手动开机 |
| ping 通但 SSH 不可达 | SSH 未启用 | Step 0 中配置 SSH |
| | 防火墙拦截 | 检查 NAS 防火墙规则 |

### 4.7.2 挂载失败

```bash
# 常见挂载错误与解决

# 错误1: "No route to host"
# → NAS IP 可能变了，检查路由器 DHCP 分配
ping $NAS_IP

# 错误2: "Permission denied"
# → 用户名密码错误或权限不足
# → 在 DSM 中检查共享文件夹权限

# 错误3: "server connection timed out"
# → SMB 服务未启动
# → DSM → 控制面板 → 文件服务 → SMB → 启用
```

### 4.7.3 NAS 关机失败

如果 SSH 远程关机失败：
1. 手动登录 DSM 网页界面关机
2. 或者长按 NAS 电源键（物理关机）
3. 检查 `/etc/sudoers` 是否允许你的用户免密执行 `poweroff`

---

## 4.8 模块独立性说明

这个模块**完全独立**于 115 备份模块：

- ✅ 可单独运行（不依赖 115 模块的任何输出）
- ✅ 可单独配置（有自己的日志文件 `backup_nas.log`）
- ✅ 可单独调试（直接运行 `backup_to_nas.sh` 即可测试）
- ✅ 可替换（如果将来换成其他 NAS 品牌或 TrueNAS，只需替换这个模块）
- ✅ 不影响主链路（NAS 备份失败不阻塞 115 备份）

---

*上一步 ← [Step 3: 备份至 115 网盘](../step-03-backup-115/README.md)* | *下一步 → [Step 6: 全自动调度](../step-06-scheduling/README.md)*
