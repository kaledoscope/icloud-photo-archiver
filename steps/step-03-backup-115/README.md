# Step 3: 备份至 115 网盘 (CloudDrive2)

> 将分类整理后的照片文件夹，通过 CloudDrive2 挂载 + rsync 差异同步到 115 网盘，实现异地热备份。

---

## 3.1 原理

```
本地备份目录                          CloudDrive2 挂载点                    115 云端
/Volumes/Data/PhotoBackup/  ──rsync──→  /Volumes/CloudDrive/115/  ──CD2──→  115 网盘
       ↑                                  ↑                                ↑
    (真实文件)                        (虚拟本地目录)                    (实际存储)
   
CloudDrive2 在后台负责：
- 将本地文件操作转化为 115 API 调用
- 维护 115 登录态
- 处理上传重试和断点续传
rsync 只需要像操作普通本地目录一样操作挂载点即可。
```

### 3.1.1 为什么选 CloudDrive2 + rsync 而不是 rclone

| 维度 | CloudDrive2 + rsync | rclone |
|------|--------------------|--------|
| 115 支持 | 官方维护，稳定 | 第三方 backend，cookie 易失效 |
| 配置难度 | GUI 登录即可 | 需手动抓 cookie |
| 操作方式 | rsync 直接操作挂载点 | rclone sync 命令 |
| 断点续传 | rsync --partial | rclone 原生支持 |
| 资源占用 | CloudDrive2 常驻 ~200MB | 按需启动 |
| 已有基础设施 | 如果 Mac 上已在用 CD2 | 需额外配置 |

**结论**：如果你已经有 CloudDrive2 在运行（大多数 115 用户都有），用它。如果追求纯 CLI/脚本化管理，可以选择 rclone。

---

## 3.2 前置条件

确认以下内容（Step 0 已完成）：

```bash
# 1. CloudDrive2 正在运行
pgrep -l CloudDrive2

# 2. 115 网盘已挂载
mount | grep CloudDrive

# 3. 挂载点可读写
ls /Volumes/CloudDrive/115/

# 4. 远端备份目录已存在
ls /Volumes/CloudDrive/115/PhotoBackup/
```

---

## 3.3 备份策略

### 3.3.1 同步模式：镜像同步

使用 `rsync --delete` 实现源端与目的端的完全镜像：

- 源端新增文件 → 同步到远端
- 源端修改文件 → 校验差异后同步
- 源端删除文件 → 远端对应删除（受 `.state/` 和 `.logs/` 排除规则保护）

### 3.3.2 rsync 参数详解

```bash
rsync -avhP \
    --checksum \            # 基于文件内容 SHA256 校验而非仅比较时间戳和大小
                            # 原因：CloudDrive2 挂载点的文件时间戳可能与本地不一致
    --partial \             # 保留未传输完成的文件（.partial 后缀），下次续传
    --delete \              # 删除远端已不存在的文件，保持镜像
    --exclude '.state/' \   # 不同步运行状态（各机器独立）
    --exclude '.logs/' \    # 不同步日志
    --exclude '.DS_Store' \ # 不同步 macOS 系统文件
    --exclude '._*' \       # 不同步 macOS 资源分支文件
    --timeout=300 \         # I/O 超时 5 分钟（挂载点偶尔卡住）
    --contimeout=60 \       # 连接超时 1 分钟
    --stats \               # 传输完成后显示统计
    "$BACKUP_ROOT/" \       # 源端（注意末尾 / 表示同步目录内容）
    "$REMOTE_DIR/"          # 目的端
```

### 3.3.3 为什么使用 --checksum

CloudDrive2 挂载为 FUSE 文件系统，文件的时间戳和权限可能与本地真实文件系统不同。默认情况下 rsync 依赖时间戳和文件大小来判断文件是否变更，这在 FUSE 挂载点上可能不可靠。

`--checksum` 强制 rsync 比较文件内容的哈希值（块级校验和），确保：
- 不会因为时间戳不一致而漏传文件
- 不会因为挂载点缓存而过期判断

代价是首次同步时需要完整读取所有文件计算校验和，但这使得同步可靠性大幅提升。

### 3.3.4 --partial 断点续传示例

```
传输大视频文件 (4GB) 到一半时网络中断：

没有 --partial:
  /Volumes/CloudDrive/115/PhotoBackup/iPhone_15_Pro/2026/06/.../video.MOV  ← 被删除
  下次运行 rsync 从头开始传输

有 --partial:
  /Volumes/CloudDrive/115/PhotoBackup/iPhone_15_Pro/2026/06/.../video.MOV.xxxxxx  ← 保留
  下次运行 rsync 从断点继续，只传剩余 2GB
```

---

## 3.4 备份脚本

脚本位置：`modules/backup/115/backup_to_115.sh`

### 3.4.1 脚本流程

```
1. 检查 CloudDrive2 挂载点是否存在
   ├── 不存在 → 记录错误，exit 1
   └── 存在 → 继续
        │
2. 检查挂载点是否可写
   ├── 不可写 → 记录错误，exit 1
   └── 可写 → 继续
        │
3. 确保远端备份目录存在
        │
4. 执行 rsync 差异同步
   ├── 成功 (exit 0) → 记录成功
   └── 失败 (exit ≠ 0) → 记录失败，exit 非零
        │
5. 更新 manifest 中的 backed_up_115 标记
```

### 3.4.2 使用方式

```bash
# 直接执行
bash modules/backup/115/backup_to_115.sh

# 指定备份根目录
BACKUP_ROOT=/custom/path bash modules/backup/115/backup_to_115.sh

# 在流水线中调用
source config/config.env
bash modules/backup/115/backup_to_115.sh
```

### 3.4.3 输出示例

```
[2026-06-20 03:10:01] ===== 115 备份开始 =====
[2026-06-20 03:10:01] CloudDrive2 挂载点正常: /Volumes/CloudDrive/115
sending incremental file list
iPhone_15_Pro/2026/06/personal/photos/IMG_5678.HEIC
          1.23M 100%    0.00kB/s    0:00:00 (xfr#1, to-chk=126/127)
...
Number of files: 127 (reg: 125, dir: 2)
Total file size: 1.42 GB
Total transferred file size: 342 MB
Literal data: 342 MB
Matched data: 0 bytes

[2026-06-20 03:25:30] ✅ 115 备份完成 (耗时 15分29秒)

# 备份后的 115 目录结构:
# /Volumes/CloudDrive/115/PhotoBackup/
# ├── iPhone_15_Pro/2026/06/personal/photos/...
# ├── Pixel_8/2026/06/personal/photos/...
# └── ...
```

---

## 3.5 故障处理

### 3.5.1 CloudDrive2 挂载点消失

**症状**：
```
❌ CloudDrive2 未挂载到 /Volumes/CloudDrive/115
```

**原因**：
- CloudDrive2 进程崩溃
- 115 登录态过期
- macOS 重启后 CloudDrive2 未自动启动

**解决**：
1. 检查 CloudDrive2 是否在运行：`pgrep -l CloudDrive2`
2. 如果未运行，启动 CloudDrive2.app
3. 重新挂载 115 网盘
4. 检查 CloudDrive2 的开机自启设置

### 3.5.2 rsync 传输速度慢

**症状**：rsync 速度远低于网络带宽。

**可能原因与解决**：

| 原因 | 解决 |
|------|------|
| CloudDrive2 限速 | 在 CloudDrive2 设置中调整上传限速 |
| FUSE 层开销 | 正常现象，预计损失 10-20% 效率 |
| 大量小文件 | 小文件（< 1MB）的传输效率天然低，无法避免 |
| --checksum 开销 | 每次同步需读取全部文件计算哈希，可考虑间隔使用 |

### 3.5.3 115 空间不足

**症状**：rsync 报写入错误。

**检查**：
```bash
# 查看 115 网盘可用空间
# 在 CloudDrive2 界面中查看，或通过 115 官方 App
```

**解决**：
- 扩容 115 网盘（购买更多空间）
- 调整 `EXPORT_WINDOW_MONTHS` 减小备份窗口
- 手动清理 115 上的远端旧数据（**不要**在本地删除后靠 rsync 同步 —— 本系统不用 `--delete`，删不掉远端）

---

## 3.6 验证备份完整性

### 3.6.1 随机抽样验证

```bash
# 使用 verify_backup.py 验证远端文件完整性
python3.11 modules/export/verify_backup.py --target 115

# 会随机抽取 50 个文件，对比本地与远端的 SHA256
```

### 3.6.2 手动验证

```bash
# 随机选一个文件验证
LOCAL_FILE="/Volumes/Data/PhotoBackup/iPhone_15_Pro/2026/06/personal/photos/IMG_1234.HEIC"
REMOTE_FILE="/Volumes/CloudDrive/115/PhotoBackup/iPhone_15_Pro/2026/06/personal/photos/IMG_1234.HEIC"

# 比较哈希
shasum -a 256 "$LOCAL_FILE"
shasum -a 256 "$REMOTE_FILE"
# 两个哈希值应该完全一致
```

---

## 3.7 模块独立性说明

这个模块 **完全独立** 于 NAS 备份模块：

- ✅ 可单独运行（不依赖 NAS 模块的任何输出）
- ✅ 可单独配置（有自己的日志文件 `backup_115.log`）
- ✅ 可单独调试（直接运行 `backup_to_115.sh` 即可测试）
- ✅ 可替换（如果将来换用 rclone 或其他网盘，只需替换这个模块）

与 NAS 备份的唯一联系是通过 `backup_manifest.json` 中的 `backed_up_115` 标记，这个标记被清理模块（Step 5）用来判断双备份是否完成。

---

*上一步 ← [Step 2: 照片导出与分类](../step-02-photo-export/README.md)* | *下一步 → [Step 4: 备份至群晖 NAS](../step-04-backup-nas/README.md)*
