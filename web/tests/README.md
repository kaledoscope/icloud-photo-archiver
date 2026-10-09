# web/tests — 浏览器端到端验证

用 puppeteer 驱动系统 Chrome，真点一遍界面。**不是单元测试** —— 这里验证的是
「后端起子进程调 CLI → 解析 `[RESULT]` → SSE 推给前端 → 界面渲染」这条完整链路，
以及源目录在整条链路里始终没被改动。

| 脚本 | 做什么 | 会不会写盘 |
|------|--------|-----------|
| `ui-smoke.mjs` | 扫描 → 审阅 → 筛选 → 分页 → 设备命名 → Esc → **预演** | 只往 `PHOTO_OUT` 写日志截图，不复制媒体 |
| `ui-apply.mjs` | 扫描 → **真执行复制** | ⚠️ **会真往 `PHOTO_OUT` 写文件** |

## 用法

需要一个已经在跑的实例（`web/deploy.sh --run` 或 `--install`）：

```bash
cd web/frontend
npm i -D puppeteer-core        # 首次

PHOTO_SRC=~/Pictures/some_album PHOTO_OUT=/tmp/ui-test \
  node ../tests/ui-smoke.mjs http://127.0.0.1:8787
```

| 环境变量 | 说明 |
|----------|------|
| `PHOTO_SRC` | 源目录（必填）。里面最好同时含「映射表里有的设备」和「读不出机型的文件」，这样命名弹窗的两个分支都能走到 |
| `PHOTO_OUT` | 输出目录。**别指向你要保留的归档目录**，`ui-apply` 会真写 |
| `CHROME_BIN` | Chrome 路径，默认 macOS 标准安装位置 |

## ⚠️ 命名写回会动真实配置

`ui-smoke.mjs` 里那步「给读不出机型的文件命名」会调 `POST /api/models/unknown`，
**写的是真实的 `config/device-models.json`**。

想验证写回又不想碰真配置，就另起一个把 `DEVICE_MODELS_PATH` 指向副本的实例：

```bash
DEVICE_MODELS_PATH=/tmp/models-copy.json \
  .venv/bin/python -m uvicorn app:app --app-dir web/backend --port 8788

cp config/device-models.json /tmp/models-copy.json
PHOTO_SRC=... node web/tests/ui-smoke.mjs http://127.0.0.1:8788
```

## 源目录零改动怎么验

脚本本身不碰源目录，但值得独立核对一遍 —— 这是本项目最硬的一条约束：

```bash
SRC=~/Pictures/some_album
find "$SRC" -type f -exec stat -f '%N %m %z' {} \; | sort > /tmp/before.txt
# 跑脚本
find "$SRC" -type f -exec stat -f '%N %m %z' {} \; | sort > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt && echo "✅ 源文件零改动"
```

截图落在 `/tmp/ui-*.png`。
