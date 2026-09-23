# Step 0: 环境准备

> 一次性配置，为整个备份系统打好基础。覆盖 Mac Mini 系统设置、Photos.app、CloudDrive2、NAS 和目录结构。

---

## 0.1 Mac Mini 基础配置

### 0.1.1 禁止休眠（作为服务器运行）

Mac Mini 需要 24x7 运行，确保任何时间都能执行备份任务：

```bash
# 禁止系统休眠
sudo pmset -a sleep 0

# 禁止硬盘休眠
sudo pmset -a disksleep 0

# 显示器可休眠（省电）
sudo pmset -a displaysleep 30

# 验证当前电源设置
pmset -g
```

### 0.1.2 开机自动登录

断电重启后需要自动恢复服务，必须开启自动登录：

1. 打开 **系统设置 → 用户与群组 → 登录选项**
2. 在「自动登录」中选择你的管理员账户
3. 输入密码确认

> ⚠️ **安全提示**：自动登录意味着物理接触 Mac 的人可以访问你的数据。确保 Mac Mini 放在安全的位置（如上锁的机柜）。

### 0.1.3 关闭自动更新重启

系统自动更新重启会打断备份任务：

```bash
# 禁止 macOS 自动安装更新（仍会收到通知）
sudo defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticallyInstallMacOSUpdates -bool false

# 禁止自动重启
sudo defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticRestart -bool false
```

### 0.1.4 安装依赖

```bash
# 1. 安装 Homebrew（如未安装）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. 安装 osxphotos（照片导出核心）
pip3 install osxphotos

# 3. 安装 wakeonlan（NAS 唤醒）
brew install wakeonlan

# 4. 验证安装
python3.11 -c "import osxphotos; print('osxphotos', osxphotos.__version__)"
which wakeonlan
```

---

## 0.2 Photos.app 配置

这是整个系统最关键的一步配置——如果没设对，导出的全是低分辨率缩略图。

### 0.2.1 基础设置

1. 打开 **照片 App**（在 Mac 上）
2. 菜单栏 → **照片 → 设置**（或 `⌘,`）
3. 点击 **iCloud** 标签页
4. ✅ 勾选 **「iCloud 照片」**
5. ✅ 选择 **「将原片下载到此 Mac」** ← **关键！**

### 0.2.2 验证配置

```bash
# 检查 Photos 库中的文件是否为原片
# 打开 Finder → 图片 → 右键「Photos Library.photoslibrary」→ 显示包内容
# 进入 originals/ 目录，检查文件大小是否正常（HEIC 通常 2-5MB，非几百KB）

# 或者用命令行检查
du -sh ~/Pictures/Photos\ Library.photoslibrary/
# 如果库大小远小于 iCloud 照片总量，说明可能只下载了缩略图
```

### 0.2.3 等待初始下载

首次开启「下载原片」后，Mac 需要从 iCloud 下载所有照片的原片。这个过程的时长取决于：

- iCloud 照片总量
- 网络带宽
- Mac 存储速度

```bash
# 在 Photos.app 底部可以看到下载进度
# 例如："正在下载 1,234 个原片..."

# 也可以用 osxphotos 检查未完成下载的数量
python3.11 -c "
import osxphotos
photosdb = osxphotos.PhotosDB()
pending = sum(1 for p in photosdb.photos() if p.iscloudasset and not p.hasadjustments)
print(f'未完成下载: {pending} 个项目')
"
```

> ⏱️ **预计时间**：100GB 照片库在千兆网络下大约需要 2-4 小时完成初始下载。

---

## 0.3 CloudDrive2 配置

### 0.3.1 下载与安装

1. 访问 [CloudDrive2 官网](https://www.clouddrive2.com)
2. 下载 macOS 版本（.dmg 文件）
3. 安装并拖入「应用程序」文件夹
4. 首次启动时，macOS 可能提示安全警告，需在 **系统设置 → 隐私与安全性** 中允许

### 0.3.2 登录 115 网盘

1. 启动 CloudDrive2
2. 点击菜单栏图标 → **添加云存储**
3. 选择 **115 网盘**
4. 使用 115 账户登录（支持扫码或账号密码）
5. 授权 CloudDrive2 访问 115 网盘

### 0.3.3 挂载设置

1. 在 CloudDrive2 界面中，选择已登录的 115 网盘
2. 点击 **挂载** → 选择挂载模式
3. 设置挂载路径：`/Volumes/CloudDrive/115`
4. 选择挂载类型：**本地目录** 模式
5. 勾选 ✅ **开机自动挂载**
6. 勾选 ✅ **CloudDrive2 开机自启**

### ⚠️ 0.3.4 验证挂载模式（WebDAV vs FUSE）

CloudDrive2 有两种挂载模式，**rsync 只能用于 FUSE 本地目录模式**：

```
FUSE 本地目录:  mount 显示 "macfuse"     → ls 正常，rsync 可用 ✅
WebDAV 网络挂载: mount 显示 "webdav"     → ls 报 Operation not permitted ❌
```

```bash
# 检查你的挂载类型
mount | grep -E "CloudDrive|dav"

# 如果是 FUSE (macfuse):
#   CloudDrive2@macfuse0 on /Volumes/CloudDrive/115 (macfuse, ...)
#   → ✅ 正确，rsync 可用

# 如果是 WebDAV (webdav):
#   http://localhost:19798/dav/ on /Volumes/dav (webdav, ...)
#   → ❌ 错误！终端无法访问，rsync 不可用
#   → 解决：在 CloudDrive2 中删除 WebDAV 挂载，改用「本地目录」模式重新挂载
```

```bash
# 验证终端可访问（FUSE 模式）
ls /Volumes/CloudDrive/115/

# 测试写入
echo "test" > /Volumes/CloudDrive/115/test.txt && rm /Volumes/CloudDrive/115/test.txt
echo "✅ CloudDrive2 挂载正常"
```

### 0.3.5 创建远端备份目录

```bash
# 在 115 网盘上创建备份专用目录
mkdir -p /Volumes/CloudDrive/115/PhotoBackup
```

### 0.3.6 CloudDrive2 常见问题

| 问题         | 解决方案                                                   |
| ------------ | ---------------------------------------------------------- |
| 挂载点消失   | CloudDrive2 可能崩溃。重新启动 CloudDrive2，检查登录状态   |
| 写入权限被拒 | 检查 115 网盘容量是否已满；确认 CloudDrive2 有完全访问权限 |
| 挂载点只读   | 在 CloudDrive2 设置中检查挂载模式是否为「读写」            |
| 115 登录过期 | 重新登录。115 的 cookie 有效期通常为 30-90 天              |

---

## 0.4 群晖 NAS 配置

### 0.4.1 网络配置（建议静态 IP）

1. 登录 DSM（群晖管理界面）
2. **控制面板 → 网络 → 网络界面**
3. 为 NAS 设置静态 IP 地址（如 `192.168.1.100`），确保不会因 DHCP 续约变化

### 0.4.2 启用 Wake-on-LAN (WOL)

1. DSM → **控制面板 → 硬件和电源**
2. **电源管理** 标签页
3. ✅ 勾选 **「启用局域网唤醒 (WOL)」**
4. 记录 NAS 的 **MAC 地址**（信息中心 → 网络 → MAC 地址）
   - 格式类似：`00:11:32:AA:BB:CC`

```bash
# 在 Mac 上测试 WOL
wakeonlan 00:11:32:AA:BB:CC

# 等待 1-2 分钟后检查 NAS 是否在线
ping -c 3 192.168.1.100
```

### 0.4.3 启用 SSH 服务

1. DSM → **控制面板 → 终端机和 SNMP**
2. **终端机** 标签页
3. ✅ 勾选 **「启用 SSH 服务」**
4. 端口保持默认 22（或自定义）

### 0.4.4 配置 SSH 免密登录

```bash
# 1. 在 Mac 上生成专用 SSH 密钥对
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_synology -N ""

# 2. 将公钥复制到 NAS
ssh-copy-id -i ~/.ssh/id_ed25519_synology.pub your_nas_user@192.168.1.100

# 3. 配置 SSH 快捷别名
cat >> ~/.ssh/config << 'EOF'
Host synology-nas
    HostName 192.168.1.100
    User your_nas_user
    IdentityFile ~/.ssh/id_ed25519_synology
    ConnectTimeout 10
    ServerAliveInterval 60
    StrictHostKeyChecking accept-new
EOF

# 4. 测试免密登录
ssh synology-nas "echo 'SSH OK'"
```

### 0.4.5 创建共享文件夹

1. DSM → **控制面板 → 共享文件夹**
2. 点击 **新增**
3. 名称：`photo_backup`
4. 位置：选择存储池和卷
5. 权限：确保你的 NAS 用户有 **读写** 权限
6. ✅ 勾选 **「启用回收站」**（额外的安全网——误删可在 NAS 端恢复）

### 0.4.6 测试 SMB 挂载

```bash
# 在 Mac 上测试挂载 NAS 共享
mkdir -p /Volumes/NAS_Backup
mount -t smbfs "//your_nas_user@192.168.1.100/photo_backup" /Volumes/NAS_Backup

# 验证
ls /Volumes/NAS_Backup/

# 测试完成，卸载
diskutil unmount /Volumes/NAS_Backup
```

### 0.4.7 NAS 关机命令

群晖 NAS 支持以下关机方式：

```bash
# 方式 1：synopoweroff（推荐，群晖专用）
ssh synology-nas "sudo synopoweroff"

# 方式 2：标准 poweroff
ssh synology-nas "sudo poweroff"

# 方式 3：通过 DSM API（无需 SSH 权限）
curl -s "http://192.168.1.100:5000/webapi/entry.cgi?api=SYNO.Core.Shutdown&version=1&method=shutdown"
```

> 建议先在 NAS 上配置 `sudo` 免密（`/etc/sudoers`），否则关机指令会要求输入密码。

---

## 0.5 创建本地备份目录

```bash
# 备份根目录（建议在外置 SSD 或大容量内置卷上）
BACKUP_ROOT="/Volumes/Data/PhotoBackup"
mkdir -p "$BACKUP_ROOT"/{.state,.logs}

# 如果使用其他路径，记下来，后续配置 config.env 时需要
echo "BACKUP_ROOT=$BACKUP_ROOT"
```

### 0.5.1 目录结构预览

配置完成后，完整的目录结构如下：

```
/Volumes/Data/PhotoBackup/              # 备份根目录
├── iPhone_15_Pro/                       # 设备 A
│   └── YYYY/
│       └── MM/
│           ├── personal/
│           │   ├── photos/
│           │   └── videos/
│           └── family-shared/
│               ├── photos/
│               └── videos/
├── Pixel_8/                  # 设备 B
│   └── YYYY/MM/...
├── iPhone_13/                          # 设备 C
│   └── YYYY/MM/...
├── Unknown_Device/                     # 无法识别设备（截图等）
│   └── ...
├── .state/                             # 运行状态文件
│   ├── last_export.json
│   ├── last_nas_backup.json
│   └── backup_manifest.json
└── .logs/                              # 运行日志
    ├── pipeline.log
    ├── export.log
    ├── backup_115.log
    ├── backup_nas.log
    └── cleanup.log
```

> **设计原则**：设备优先。家庭场景中机型=人，每个人打开备份第一个看到的就是自己的文件夹。详细论述见 [Step 2: 照片导出与分类](../step-02-photo-export/README.md#221-第一性原理为什么设备优先)。

---

## 0.6 配置检查清单

在进入下一步之前，确认以下每一项：

- [ ] Mac 永不休眠（`pmset -g` 确认）
- [ ] 开机自动登录已开启
- [ ] 自动更新重启已关闭
- [ ] osxphotos 已安装（`python3.11 -c "import osxphotos"`）
- [ ] wakeonlan 已安装（`which wakeonlan`）
- [ ] Photos.app 设置为「下载原片到此 Mac」
- [ ] iCloud 原片已完成初始下载
- [ ] CloudDrive2 已安装并登录 115
- [ ] 115 网盘已挂载到 `/Volumes/CloudDrive/115`
- [ ] CloudDrive2 设置为开机自启
- [ ] NAS 已设置静态 IP
- [ ] NAS WOL 已启用，已知 MAC 地址
- [ ] NAS SSH 已启用，免密登录已配置
- [ ] NAS 共享文件夹 `photo_backup` 已创建
- [ ] Mac 可成功挂载/卸载 NAS SMB 共享
- [ ] 备份目录 `/Volumes/Data/PhotoBackup` 已创建

---

_下一步 → [Step 1: iCloud → Mac 同步](../step-01-icloud-sync/README.md)_
