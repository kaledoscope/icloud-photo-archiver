# modules/classify — 分类引擎

把「一个媒体文件」判定成「归档树里的目标路径」，**不依赖 Photos 库**。

与 `modules/export` 的分工：

| 模块 | 职责 | 依赖 |
|------|------|------|
| `export/` | 从 Photos.app **取**素材 | osxphotos |
| `classify/` | 判定素材**去哪** | 无（只看文件本身） |

因为取与判分离，Photos.app 导出的照片和硬盘上散落多年的老素材走**同一套分类规则**。

---

## 分类树

```
{output}/{设备型号}/{YYYY}/{MM}/{来源}/{photos|videos}/{文件名}
```

第一性原理：家庭场景中机型 = 人，最外层按「谁拍的」组织，每人一个顶层文件夹，
115 网盘上可以直接按人分享。

```
iPhone_15_Pro/2025/02/personal/photos/IMG_0001.JPG
iPhone_15_Pro/2025/02/personal/videos/IMG_0001.MOV
Unknown_Device/2019/11/personal/videos/sd1791027469_2.mp4
```

---

## 组成

| 文件 | 职责 |
|------|------|
| `metadata.py` | 读文件本身的元数据（机型 / 拍摄时间 / 照片还是视频） |
| `device.py` | 机型字符串 → 归档文件夹名（映射表 `config/device-models.json`） |
| `planner.py` | 五级路径构建 + 冲突处理 + 统计 |
| `scan.py` | CLI：扫描目录 → **分类计划**（只读，不复制） |
| `apply.py` | CLI：按计划**复制**（源文件只读） |

---

## 用法

```bash
export PYTHONPATH="$PWD"

# 1. 扫描，产出计划（只读源目录，不创建任何文件）
python -m modules.classify.scan \
    --input  ~/Pictures/old_media \
    --output ~/Classified \
    --plan-out /tmp/plan.json

# 2. 审阅计划后执行复制
python -m modules.classify.apply --plan /tmp/plan.json
```

常用参数：

| 参数 | 作用 |
|------|------|
| `--jobs N` | 并发读取元数据的线程数（默认 8） |
| `--limit N` | 只处理前 N 个文件（调试用） |
| `--dry-run` | `apply` 只统计不复制 |

---

## 结果行

```
[PROGRESS] done=100 total=254
[RESULT] planned=253 skipped=1 failed=0 total_bytes=11539905143 tools=ok
[RESULT] copied=253 skipped=0 failed=0 bytes=11539905143
```

编排方（shell 脚本、网页后端）提取这两行，不要解析中文日志。

`tools=` 是外部工具的可用性标记，`ok` 或 `missing:ffprobe`。它存在的理由是
**失败会伪装成成功**：`ffprobe` 缺失时视频读不出机型和拍摄时间，会被静默归到
`Unknown_Device` 并用 mtime 当日期，而扫描照样返回 `planned=65 failed=0`。
调用方看到 `tools=missing:ffprobe` 就该知道这份结果不可信。

---

---

## 依赖

**零新增 Python 依赖**，只用 macOS 自带命令：

| 用途 | 命令 | 说明 |
|------|------|------|
| 图片元数据 | `sips` | 原生支持 HEIC/HEIF；一次只接受一个文件 |
| 视频元数据 | `ffprobe` | 读 `com.apple.quicktime.model` 与 `creationdate` |

用 `ffprobe` 而非 Python 图像库，是因为 iPhone 直出是 HEIC，而 Pillow 处理 HEIC
要额外装 `pillow-heif`；sips 是 Apple 自家工具，格式支持天然最全。

> ⚠️ 视频时间优先取 `com.apple.quicktime.creationdate`（带时区）而非 `creation_time`（UTC）。
> 晚上 7 点在东京拍的片子若按 UTC 算会变成当天 10 点，跨午夜时直接落进错误的年月目录。

### 找这两个命令的顺序

`metadata.py` 的 `_locate()` 按三步找：

1. `config/config.env` 里的 `FFPROBE_BIN` / `SIPS_BIN`（显式指定，优先级最高）
2. `shutil.which()` —— 走当前 `PATH`
3. 依次翻 `/opt/homebrew/bin`、`/usr/local/bin`、`/opt/local/bin`、`/usr/bin`、`/bin`

第 3 步的兜底不是可有可无的。**launchd 的默认 `PATH` 只有
`/usr/bin:/bin:/usr/sbin:/sbin`**，而 `ffprobe` 由 Homebrew 装在
`/opt/homebrew/bin`。服务化部署（`web/` 的 launchd job）跑在 launchd 里，
没有兜底就会整批视频静默降级 —— 这个坑真实踩过，见 `web/README.md`。

`sips` 在 `/usr/bin`，所以图片侧从来不受影响，**只有视频会坏**，更难察觉。

---

## 安全保证

1. **源文件只读** —— 全模块不存在对源路径的写 / 删 / 移调用
2. **绝不覆盖** —— 目标同名时追加拍摄时间戳，仍冲突则加递增序号
3. **越界写入拦截** —— 目标路径必须落在计划声明的 `output_dir` 之内
4. **目录关系校验** —— 输入输出互为包含时直接拒绝，防止自我递归拷贝
5. **无删除逻辑** —— 本模块没有 `rm` / `unlink` / `rmtree` / trash
6. **不跟随符号链接** —— 避免目录环与越出源树
7. **两步执行** —— 先出计划供审阅，再执行，不存在一键直拷

---

## 无元数据时的行为

| 情况 | 处理 |
|------|------|
| 读不出机型 | 归入 `Unknown_Device/`（**不使用** `DEFAULT_DEVICE_MODEL`） |
| 读不出拍摄时间 | 用文件 mtime 兜底，并在统计里标明 `by_date_source.mtime` |
| `.aae` 旁车文件 | 跟随同名主文件；Live Photo 同名词干时**跟随照片**（`.aae` 是图像修图参数） |
| `.DS_Store` / `._*` | 跳过 |
| 其他扩展名 | 跳过并计入 `unsupported-ext` |

`DEFAULT_DEVICE_MODEL` 是给 Photos 库用的（那批基本是机主手机存的网图）；
硬盘上的老素材来源不明，硬塞进主力机型会伪造出「这是机主拍的」的假象。

「读不出机型」那批的落点文件夹可以改：在 `config/device-models.json` 里写一个
`_unknown_folder_name` 键，`device.unknown_folder_name()` 就会返回它；没有这个键时
行为与从前逐字节一致（仍是 `Unknown_Device`）。网页界面的「设备命名」弹窗走的就是这条路径。
