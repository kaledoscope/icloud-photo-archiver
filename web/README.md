# web/ — 图形界面

给 `modules/classify` 套一层网页界面：**选目录 → 扫描 → 审阅计划 → 复制**。

v1 只做文件夹分类，不含备份与定时。备份流水线仍在 `modules/scheduler/`，走命令行。

---

## 它为什么长这样

**这个 web 层只是「又一个调用方」。** 它不 import 任何分类逻辑，只起子进程跑
`python -m modules.classify.scan`，然后读 `[RESULT]` 行 —— 和
`modules/scheduler/photo_backup_weekly.sh` 的调用方式完全一样。

这不是洁癖，是宪法铁律 Ⅱ（功能模块化）的直接后果：**把 `web/` 整个目录删掉，
命令行系统照常工作**。反过来说，网页界面永远不可能和 CLI 走出两套分类规则，
因为规则只存在于 `modules/classify/` 一处。

```
浏览器 ──HTTP──▶ FastAPI（web/backend）
                      │  subprocess
                      ▼
              python -m modules.classify.scan   ← 唯一的规则来源
                      │  stdout: [PROGRESS] / [RESULT]
                      ▼
              FastAPI 解析 → SSE → 浏览器
```

---

## 用法

```bash
# 日常启动（前台，Ctrl-C 停）
web/deploy.sh --run

# 装成常驻自启（launchd）
web/deploy.sh --install
web/deploy.sh --status
web/deploy.sh --uninstall        # 只从 launchd 摘除，不动任何文件

# 开发（后端 --reload + 前端热更新）
web/dev.sh
```

装好后浏览器开 **http://127.0.0.1:8787**。

| 命令 | 作用 |
|------|------|
| `web/deploy.sh` | 只构建前端 |
| `web/deploy.sh --install` | 构建 + 渲染 plist + 装 launchd 服务 |
| `web/deploy.sh --status` | 服务状态 + 外部工具是否就位 |
| `web/deploy.sh --run` | 前台跑，方便看日志 |
| `web/dev.sh` | 后端 8787 + 前端 5173，均带热更新 |

`dev.sh` 下前端在 5173，通过 Vite 代理把 `/api` 转给 8787。

---

## 目录

```
web/
├── backend/
│   ├── app.py            FastAPI 应用：API + 托管前端静态文件
│   ├── jobs.py           内存任务表 + 可订阅事件流（SSE 的来源）
│   ├── runner.py         起子进程跑 CLI，解析 [PROGRESS] / [RESULT]
│   ├── models_store.py   读写 config/device-models.json（界面上的设备命名）
│   └── requirements.txt  fastapi / uvicorn / pydantic
├── frontend/             Svelte 5 + Vite + TypeScript
│   └── src/
│       ├── App.svelte        主流程编排
│       ├── lib/api.ts        所有 fetch 与 SSE 订阅
│       ├── lib/types.ts      与后端应答结构一一对应
│       └── components/       DirBrowser / DirField / DeviceNaming / PlanReview / ProgressLog
├── launchd/              常驻服务模板（占位符由 deploy.sh 渲染）
├── deploy.sh             构建 / 安装 / 卸载 / 状态
├── dev.sh                开发模式
└── .state/               计划文件、日志（已 gitignore）
```

**单进程部署**：FastAPI 同时提供 API 和构建后的前端。只有一条命令、一个端口。

---

## API

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/api/health` | 服务状态 + 外部工具是否就位 |
| GET | `/api/browse?path=` | 列子目录（给目录选择器用） |
| GET | `/api/models` | 机型映射表 |
| POST | `/api/models` | 新增/更新一条映射（写回 JSON） |
| POST | `/api/models/unknown` | 指定「读不出机型」那批的落点文件夹 |
| POST | `/api/scans` | 开始扫描 → `{job_id}` |
| GET | `/api/jobs` | 最近的任务 |
| GET | `/api/jobs/{id}` | 单个任务状态 |
| GET | `/api/jobs/{id}/events` | **SSE** 进度流 |
| GET | `/api/plans/{id}` | 计划（分页，可按设备/类型过滤） |
| POST | `/api/plans/{id}/apply` | 按计划复制 → `{job_id}` |

**没有、也不会有任何删除类接口。** `/openapi.json` 里查不到一个 `DELETE`。

---

## 安全保证

界面上不存在的操作，就是做不到的操作：

1. **两步执行** —— 扫描只读元数据、只产出计划；复制要再点一次。没有「一键直拷」。
2. **源目录只读** —— 整个 web 层不碰源文件，复制由 `modules/classify/apply.py` 执行，
   那里也不存在对源路径的写/删/移。
3. **无删除接口** —— 前后端都没有删除入口。
4. **只绑回环地址** —— 服务没有登录、没有鉴权，`127.0.0.1` 之外访问不到。
5. **计划落盘、前端分页取** —— 上万文件时计划有数 MB，不适合整个塞进浏览器。

---

## 踩过的坑：launchd 下视频静默降级

**症状**：服务化部署后跑扫描，任务报 `state: done`、`failed=0`，但结果不对 ——
65 个文件里 43 个视频全被归到 `Unknown_Device`，日期用的是文件修改时间（mtime）。

**根因**：`ffprobe` 由 Homebrew 装在 `/opt/homebrew/bin`，而 **launchd 的默认 `PATH`
只有 `/usr/bin:/bin:/usr/sbin:/sbin`**。子进程找不到 `ffprobe`，读不到
`com.apple.quicktime.model` 和 `creationdate`，于是走了「读不出元数据」的兜底路径。

`sips` 在 `/usr/bin`，所以**图片侧完全正常** —— 只有视频坏，而任务照样报成功。

**三层修复**（任何一层单独都能挡住，但都留着）：

1. `modules/classify/metadata.py` 的 `_locate()` 会依次翻几个常见 bin 目录，不只看 `PATH`。
2. plist 里显式给了一份 `PATH`，让将来新增的其他外部命令也能被找到。
3. `[RESULT]` 带 `tools=` 字段、`/api/health` 报 `missing_tools`，界面顶部弹红条 ——
   **让「静默」不再静默**。

**验证方式**（复现这个坑最快的一条路）：

```bash
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin sh -c 'command -v ffprobe'   # 找不到
```

---

## 配置

| 键 | 写在哪 | 作用 |
|----|--------|------|
| `FFPROBE_BIN` | `config/config.env` | 显式指定 ffprobe 路径（优先级最高） |
| `SIPS_BIN` | `config/config.env` | 同上；sips 在 `/usr/bin`，一般用不着 |
| `_unknown_folder_name` | `config/device-models.json` | 读不出机型的文件归到哪个文件夹 |

配置优先级：`环境变量 > config.env > secrets.env > 代码默认值`。
改了 `config.env` 后要重启服务（`web/deploy.sh --install`）。

---

## 前端开发

```bash
cd web/frontend
npm install
npm run dev        # 热更新
npm run build      # 产出 dist/，后端会直接托管
npx svelte-check   # 类型检查
```

Svelte 5 用 runes（`$state` / `$derived` / `$props` / `$effect`）。
注意 **render 期间不要写 state** —— `DeviceNaming.svelte` 的草稿对象因此在
`load()` 里构建，而不是在模板的 `{@const}` 里。
