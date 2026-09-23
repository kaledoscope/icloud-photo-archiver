# Step 2: 照片导出与分类整理

> 整个系统的核心逻辑。从 Photos.app 中导出原片，按 **年/月 → 来源 → 设备型号 → 照片/视频** 四级分类存放。

---

## 2.1 导出策略

```
                    首次运行？
                   /         \
                 是           否
                 |            |
          全量导出        增量导出
       (所有照片)     (自上次时间戳之后)
                 \         /
                  → 统一四级分类规则 →
```

### 2.1.1 全量模式（首次运行）

- 从 Photos 库导出所有照片视频
- 受 `EXPORT_WINDOW_MONTHS` 窗口限制（默认 3 个月）
- 首次运行后，下次自动切换为增量模式

### 2.1.2 增量模式（日常运行）

- 读取 `.state/last_export.json` 中的 `last_export_ts`
- 仅导出 `date_added > last_export_ts` 的项目
- 基于 `backup_manifest.json` 中的 asset UUID 去重

---

## 2.2 五级分类规则（核心设计）

### 2.2.1 第一性原理：为什么设备优先

在家庭场景中，**机型 = 人**。每个人打开备份时的第一个问题不是「这是哪年的」，而是「我的照片在哪」。

```
消费者的自然浏览路径:
  打开备份 → 看到 {我的设备} → 点进去 → 按时间浏览 → 按场景筛选

如果按年/月在最外层:
  打开备份 → 2026/ → 06/ → personal/ → 终于看到 iPhone_15_Pro/ → 点进去
  另一台: 打开备份 → 2026/ → 06/ → personal/ → Pixel_8/ → ...
  ↑ 每个人都要翻遍所有年份才能找到自己的照片，反人类

如果按设备在最外层:
  打开备份 → iPhone_15_Pro/ → 2026/06/ → personal/photos/
  另一台: 打开备份 → Pixel_8/ → 2026/06/ → personal/photos/
  ↑ 一人一个文件夹，直截了当
```

### 2.2.2 分类层级

```
层级 1: 设备型号 (device)      ← 谁拍的（家庭场景中机型=人，最自然的入口）
层级 2: 年份 (YYYY)            ← 来自 EXIF 拍摄日期
层级 3: 月份 (MM)              ← 来自 EXIF 拍摄日期
层级 4: 来源 (source)          ← personal / family-shared
层级 5: 媒体类型 (media_type)  ← photos / videos
```

### 2.2.3 目录结构示例

```
/Volumes/Data/PhotoBackup/
├── iPhone_15_Pro/                    ← 设备 A
│   ├── 2026/
│   │   ├── 04/
│   │   │   ├── personal/
│   │   │   │   ├── photos/
│   │   │   │   │   ├── IMG_1234.HEIC
│   │   │   │   │   └── IMG_1235.HEIC
│   │   │   │   └── videos/
│   │   │   │       └── IMG_1236.MOV
│   │   │   └── family-shared/
│   │   │       ├── photos/
│   │   │       └── videos/
│   │   └── 05/
│   │       └── ...
│   └── ...
│
├── Pixel_8/                ← 设备 B
│   └── 2026/
│       └── 06/
│           ├── personal/
│           │   ├── photos/
│           │   └── videos/
│           └── family-shared/
│               ├── photos/
│               └── videos/
│
├── iPhone_13/                        ← 设备 C
│   └── 2026/
│       └── ...
│
└── Unknown_Device/                   ← 无法识别设备（截图、导入等）
    └── 2026/
        └── ...
```

### 2.2.4 云盘视角：115 网盘结构

设备优先的目录结构使得云盘分享变得自然：

```
/Volumes/CloudDrive/115/PhotoBackup/
├── iPhone_15_Pro/          ← 分享这个文件夹给 A（链接或权限）
├── Pixel_8/      ← 分享这个文件夹给 B
├── iPhone_13/              ← 分享这个文件夹给 C
└── Unknown_Device/         ← 共享的/无法识别的
```

每个人只看到自己的文件夹，隐私清晰，不会误看别人的照片。

### 2.2.5 「来源」判定逻辑

```python
# 来源判定：检查照片所属的相簿名称
FAMILY_ALBUMS = ["Family", "家庭", "家人共享", "家庭聚会"]

def classify_source(photo) -> str:
    """判定照片来源"""
    try:
        album_names = [a.title for a in photo.album_info]
        for fa in FAMILY_ALBUMS:
            if fa in album_names:
                return "family-shared"
    except Exception:
        pass
    return "personal"
```

### 2.2.6 「设备型号」判定逻辑（新增 ⭐）

每台 iPhone/iPad 拍摄的照片在 EXIF 中记录了设备型号。因为家庭成员使用的机型不同，设备型号可以**间接识别拍摄者**。

```python
# 设备型号 -> 显示名称映射
# 配置文件: config/device-models.json

def classify_device(photo) -> str:
    """从 EXIF 提取设备型号，返回规范化的设备名称"""
    try:
        # osxphotos 获取相机型号
        exif = photo.exif_info
        if exif and hasattr(exif, 'camera_model'):
            model = exif.camera_model  # 如 "iPhone 15 Pro"
        else:
            # 备选：从原始文件名推断（部分场景）
            model = "Unknown_Device"
    except Exception:
        model = "Unknown_Device"
    
    # 规范化：空格 → 下划线，去掉特殊字符
    sanitized = model.replace(" ", "_").replace("/", "-")
    return sanitized
```

**设备型号映射** (`config/device-models.json`)：

```json
{
  "iPhone 15 Pro": {
    "owner": "机主",
    "folder_name": "iPhone_15_Pro",
    "color": "蓝色"
  },
  "iPhone 14 Pro Max": {
    "owner": "另一台",
    "folder_name": "Pixel_8",
    "color": "紫色"
  },
  "iPhone 13": {
    "owner": "设备 C",
    "folder_name": "iPhone_13",
    "color": ""
  },
  "iPad Pro (12.9-inch) (6th generation)": {
    "owner": "家庭共享",
    "folder_name": "iPad_Pro_12.9_6th",
    "color": ""
  }
}
```

### 2.2.7 「媒体类型」判定逻辑

```python
def classify_media_type(photo) -> str:
    """区分照片和视频"""
    if photo.ismovie:
        return "videos"
    return "photos"
```

---

## 2.3 导出格式配置

### 2.3.1 格式选项

| 选项 | 说明 | 推荐场景 |
|------|------|----------|
| `original` | 保留原始格式（HEIC/MOV/PNG/RAW） | **推荐**，保真度最高 |
| `jpeg` | 转换为 JPEG | 需要跨平台兼容时 |
| `heic` | 转换为 HEIC | 节省空间 |
| `current` | 使用当前编辑版本 | 经常在 Photos.app 中编辑照片时 |

### 2.3.2 Live Photo 处理

Live Photo = 一张静态照片（HEIC/JPEG）+ 一段短视频（MOV）

osxphotos 的 `live_photo=True` 参数确保：
- **成对导出**：`IMG_1234.HEIC` + `IMG_1234.MOV` 同时导出
- **文件名关联**：两个文件同名不同后缀，一眼可辨
- **可独立查看**：在 Finder/网盘中可以分别查看静态帧和动态视频
- **不拆散**：两个文件在同一目录下，移动/复制保持一致

### 2.3.3 文件命名策略

```python
# 默认：保留原始文件名
filename = photo.original_filename  # 如 "IMG_1234.HEIC"

# 同名冲突处理：追加拍摄时间戳
if target_path.exists():
    stem = Path(filename).stem       # "IMG_1234"
    suffix = Path(filename).suffix   # ".HEIC"
    ts = dt.strftime("%Y%m%d_%H%M%S")  # "20260415_143022"
    filename = f"{stem}_{ts}{suffix}"  # "IMG_1234_20260415_143022.HEIC"
```

---

## 2.4 导出流程（完整）

```
1. 连接 Photos 库
        │
2. 读取上次导出时间戳 (.state/last_export.json)
        │
3. 查询新增项目 (增量) 或全部项目 (全量)
        │
4. 按 EXPORT_WINDOW_MONTHS 过滤窗口
        │
5. 加载 .state/backup_manifest.json (去重用)
        │
6. 逐文件处理：
   ├── 6a. 提取设备型号 → 确定是「谁」拍的
   ├── 6b. 判定来源 (personal / family-shared)
   ├── 6c. 判定媒体类型 (photos / videos)
   ├── 6d. 计算目标路径: {device}/YYYY/MM/{source}/{media_type}/
   ├── 6e. 检查 manifest 去重
   ├── 6f. 调用 osxphotos export 导出原片
   ├── 6g. 计算 SHA256 哈希
   └── 6h. 写入 manifest 清单
        │
7. 更新 .state/last_export.json
        │
8. 记录日志
```

---

## 2.5 核心导出脚本

脚本位置：`modules/export/photo_export.py`

### 2.5.1 使用方式

```bash
# 正常导出（增量）
python3.11 modules/export/photo_export.py

# 预览模式（不实际写文件，dry run）
python3.11 modules/export/photo_export.py --dry-run

# 全量导出（忽略上次时间戳）
python3.11 modules/export/photo_export.py --full

# 指定备份根目录
BACKUP_ROOT=/custom/path python3.11 modules/export/photo_export.py
```

### 2.5.2 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BACKUP_ROOT` | `/Volumes/Data/PhotoBackup` | 备份根目录 |
| `EXPORT_WINDOW_MONTHS` | `3` | 导出窗口（月） |
| `FAMILY_ALBUMS` | `Family,家庭,家人共享` | 家庭相簿名称（逗号分隔） |
| `DEVICE_MODELS_PATH` | `config/device-models.json` | 设备型号映射文件 |

### 2.5.3 输出日志示例

```
[2026-06-20T03:00:01] ===== 开始导出 =====
[2026-06-20T03:00:01] Photos 库包含 15420 个项目
[2026-06-20T03:00:01] 增量模式：上次导出时间 2026-06-13T03:00:00
[2026-06-20T03:00:01] 新增项目: 127
[2026-06-20T03:00:01] 窗口期 (3个月) 内: 127 个项目
[2026-06-20T03:00:03] 已导出 100 个文件...
[2026-06-20T03:00:04] ===== 导出完成: 新增 127, 跳过 0, 耗时 3.2s =====
```

---

## 2.6 设备型号分类 —— 使用场景

### 场景 1：按家人浏览

```
"我想看家人上周拍的照片"
→ 打开 Pixel_8/2026/06/personal/photos/
→ 全是她拍的照片，没有混合其他人的
```

### 场景 2：迁移到新手机

```
"我要换 iPhone 17 了，导出我拍的所有照片"
→ rsync -av /Volumes/Data/PhotoBackup/iPhone_15_Pro/ /new_drive/
→ 只复制我拍的，一行命令搞定
```

### 场景 3：存储问责

```
"备份里怎么这么多视频？"
→ du -sh /Volumes/Data/PhotoBackup/*/2026/*/personal/videos/
→ 发现 iPhone_15_Pro 的视频占了 80GB，考虑调整拍摄设置
```

---

## 2.7 常见问题

### Q: 为什么我有些照片没有设备型号？

**原因**：
- 从其他渠道导入的照片（截图、下载、第三方 App）可能不包含 EXIF 相机信息
- 经过某些编辑软件处理后丢失了 EXIF 数据

**处理**：这些照片会被分类到 `Unknown_Device/` 文件夹，不会丢失。

### Q: 同一设备有多个名称怎么办？

**例如**：iOS 更新后 EXIF 中的设备名可能变化（极少见）。
**处理**：在 `device-models.json` 中配置别名映射，或者在分类函数中做名称归一化。

### Q: 家庭共享相簿中的照片，来源算 family-shared 还是按拍摄者？

**判定优先级**：来源判定优先于设备判定。

`family-shared` 相簿中的照片 → 来源 = `family-shared`，在此之下再按设备型号分类。

---

*上一步 ← [Step 1: iCloud → Mac 同步](../step-01-icloud-sync/README.md)* | *下一步 → [Step 3: 备份至 115 网盘](../step-03-backup-115/README.md)*
