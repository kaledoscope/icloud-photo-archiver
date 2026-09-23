# Step 1: iCloud → Mac 同步

> 这一层完全由 Apple 原生机制处理，不需要开发。只需确保配置正确 + 脚本中有完整性检查。

---

## 1.1 原理

```
iPhone 拍照/录像
      │
      │ (iCloud Photos 自动上传)
      ▼
 iCloud (Apple 数据中心)
      │
      │ ("下载原片到此 Mac" 已启用)
      ▼
Mac Mini Photos.app
      │
      │ (osxphotos 读取)
      ▼
  导出脚本
```

**苹果已经做好的事情**：只要 Mac 和 iPhone 都登录了同一个 Apple ID 且开启了 iCloud 照片，照片就会自动双向同步。我们唯一要确保的是 Mac 端选择了「下载原片」。

---

## 1.2 确认配置（已在 Step 0 完成）

回顾 Step 0 中的关键配置：

1. Photos.app → 设置 → iCloud → ✅ 「iCloud 照片」
2. Photos.app → 设置 → iCloud → ✅ 「将原片下载到此 Mac」

如果没有完成，请先回到 [Step 0: 环境准备](../step-00-environment/README.md) 完成配置。

---

## 1.3 导出前的同步等待

**唯一需要开发的保护机制**：确保导出时 Photos 库里的原片已经下载完毕。

### 1.3.1 为什么需要等待

如果 Photos.app 中还有未从 iCloud 下载完的项目，osxphotos 可能拿到**低分辨率缩略图**
（~几百KB）而非原片。这些伪原片一旦进入备份链路，真正的原片就丢失了 ——
**这是静默数据损坏的典型场景**，而且事后极难发现。

### 1.3.2 实际做法：观察 `cloudphotod` 负载

流水线 Step 3 在跑 osxphotos 之前，用**系统同步进程的 CPU 占用**判断同步是否结束：

```bash
if pgrep -f cloudphotod > /dev/null 2>&1; then
    # cloudphotod 在跑 → 轮询它的 CPU 占用
    cpu_pct=$(ps -eo pid,pcpu,comm | awk '/cloudphotod/ && !/awk/ {print $2}' | head -1)
    # CPU < 5% → 认为同步基本完成；否则 sleep 30 后重试，直到超时
fi
```

`cloudphotod` 是 macOS 负责 iCloud 照片同步的守护进程。它在搬运数据时 CPU 会明显升高，
搬完就回落 —— 这比逐个检查「哪些文件下载完了」成本低得多，也不用遍历整个照片库。

### 1.3.3 超时行为

**等待超时不会中止流水线**，只记一条告警：

```
⚠️  iCloud 同步等待超时，继续导出（可能有照片未同步）
```

这是刻意的**失败开放**设计：漏掉的照片会在下一轮（3 天后）被增量导出补上，
因为增量基线是磁盘扫描 —— 少导一次不会丢，只是晚几天。
而如果在这里 FATAL，一次网络慢就会让整周备份全停。

> 真正防丢数据的是 Step 0 的归档验证，不是这里的同步等待。
> 这里解决的是「别导出缩略图」，Step 0 解决的是「别删没备份的文件」。

---

## 1.4 常见问题

### Q: iCloud 同步很慢怎么办？

**原因**：iCloud 的同步速度受限于 Apple 服务器和你的网络带宽。大量数据初始同步尤其慢。

**解决**：
- 保持 Mac 开机 + 联网 + Photos.app 打开（后台即可）
- 避免使用 VPN（可能导致路由绕远）
- 初次同步建议分批次：先在 iPhone 上关闭部分相簿同步，完成后再开启

### Q: 怎么确认「下载原片」生效了？

```bash
# 检查 Photos 库大小
du -sh ~/Pictures/Photos\ Library.photoslibrary/

# 如果库大小接近 iCloud 照片总量 → 原片已下载
# 如果库明显偏小（如 2GB vs 应该的 200GB）→ 可能只有缩略图
```

### Q: Mac 存储不够放原片怎么办？

**方案 A（推荐）**：将 Photos 库迁移到外置存储
1. 退出 Photos.app
2. 将 `~/Pictures/Photos Library.photoslibrary` 复制到外置 SSD
3. 按住 `Option` 键打开 Photos.app，选择外置库
4. 在 Photos.app 设置中将外置库设为「系统照片库」

**方案 B**：使用 iCloud 优化存储 + osxphotos 按需导出
- 不推荐：osxphotos 在优化存储模式下可能导出缩略图

### Q: Photos.app 可以关闭吗？

**可以**。iCloud 同步在后台进行，不需要 Photos.app 前台运行。但你至少需要打开过一次 Photos.app 来触发初始同步。

---

## 1.5 与后续步骤的关系

```
Step 1 (本步)            Step 2                Step 3 & 4
──────────────────────────────────────────────────────────
iCloud → Mac 同步  ──→  照片导出与分类  ──→  双备份 (115 + NAS)
   (自动)                (osxphotos)           (rsync)
   
本步的作用：确保 Step 2 的输入（Photos 库原片）是完整的。
```

---

*上一步 ← [Step 0: 环境准备](../step-00-environment/README.md)* | *下一步 → [Step 2: 照片导出与分类](../step-02-photo-export/README.md)*
