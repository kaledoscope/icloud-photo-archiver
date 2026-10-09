#!/usr/bin/env python3.11
"""
app.py — 网页后端

对分类引擎来说，这个进程只是「又一个调用方」：起子进程跑
`python -m modules.classify.scan`，读 [RESULT] 行。它不 import 分类逻辑，
也不复刻任何规则。删掉整个 web/ 目录，命令行系统照常工作。

跑起来（开发）::

    uvicorn app:app --app-dir web/backend --reload --port 8787

跑起来（日常）::

    web/deploy.sh

接口一览::

    GET  /api/health                 服务与解释器状态
    GET  /api/browse?path=           列子目录（给界面上的目录选择器用）
    GET  /api/models                 机型映射表
    POST /api/models                 新增/更新一条映射（写回 JSON）
    POST /api/scans                  开始扫描 → {job_id}
    GET  /api/jobs                   最近的任务
    GET  /api/jobs/{id}              单个任务状态
    GET  /api/jobs/{id}/events       SSE 进度流
    GET  /api/plans/{id}             计划（分页）
    POST /api/plans/{id}/apply       按计划复制 → {job_id}

这个文件里没有、也不会有任何删除类接口。
"""

import json
import sys
import threading
from pathlib import Path
from typing import Iterator, List, Optional

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parents[1]
sys.path.insert(0, str(BACKEND_DIR))    # 让 `import jobs / runner` 无论怎么启动都成立

from fastapi import FastAPI, HTTPException, Query                        # noqa: E402
from fastapi.middleware.cors import CORSMiddleware                       # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles                              # noqa: E402
from pydantic import BaseModel, Field                                    # noqa: E402

import models_store                                                      # noqa: E402
import runner                                                            # noqa: E402
from jobs import EV_DONE, JobManager                                     # noqa: E402

# 计划落在 web/.state/plans/，与 CLI 的 BACKUP_ROOT/.state 无关
STATE_DIR = BACKEND_DIR.parent / ".state"
PLANS_DIR = STATE_DIR / "plans"
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"

# 计划可能有几万条，一次全吐给浏览器不现实；前端分页取
PAGE_DEFAULT = 200
PAGE_MAX = 2000

app = FastAPI(title="照片归档分类器", version="1.0")
jobs = JobManager()

# 开发时前端跑在 Vite 的 5173 端口，跟后端不同源
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(127\.0\.0\.1|localhost):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===== 请求体 =====

class ScanRequest(BaseModel):
    input_dir: str = Field(..., description="要分类的源文件夹")
    output_dir: str = Field(..., description="分类结果的输出根目录")
    jobs: int = Field(8, ge=1, le=32, description="并发读元数据的线程数")


class ApplyRequest(BaseModel):
    dry_run: bool = Field(False, description="只统计不复制")


class ModelRequest(BaseModel):
    exif_name: str = Field(..., description="元数据里读到的原始机型，如 iPhone 14 Pro")
    folder_name: str = Field("", description="归档文件夹名，留空则自动规范化")
    owner: str = Field("", description="归属人/备注")
    color: str = Field("", description="外观颜色，可留空")


class UnknownFolderRequest(BaseModel):
    folder_name: str = Field("", description="读不出机型的文件归到哪个文件夹；留空则恢复 Unknown_Device")


# ===== 基础信息 =====

@app.get("/api/health")
def health() -> dict:
    """服务状态。顺带报外部工具是否就位。

    这件事必须由界面显式报出来：ffprobe 缺失时视频会静默降级成
    Unknown_Device，扫描照样「成功」，不主动查就发现不了。
    """
    sys.path.insert(0, str(PROJECT_ROOT))
    from modules.classify import metadata as meta_mod   # noqa: PLC0415

    tools = meta_mod.check_tools()
    return {
        "ok": True,
        "project_root": str(PROJECT_ROOT),
        "python": runner.resolve_python_bin(),
        "frontend_built": (FRONTEND_DIST / "index.html").is_file(),
        "tools": tools,
        "missing_tools": [k for k, ok in tools.items() if not ok],
    }


@app.get("/api/browse")
def browse(path: Optional[str] = None) -> dict:
    """列出一个目录下的子目录，供界面上的目录选择器用。

    只列目录，不列文件——这里要选的是「哪个文件夹」，不是「哪个文件」。
    """
    target = Path(path).expanduser() if path else Path.home()
    try:
        target = target.resolve()
    except OSError as e:
        raise HTTPException(400, f"路径无法解析: {e}")

    if not target.is_dir():
        raise HTTPException(404, f"不是有效目录: {target}")

    subdirs: List[dict] = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    subdirs.append({"name": child.name, "path": str(child)})
            except OSError:
                continue    # 单个条目读不了不该让整个列表失败
    except PermissionError:
        raise HTTPException(403, f"没有权限读取: {target}")

    parent = target.parent if target != target.parent else None
    return {
        "path": str(target),
        "parent": str(parent) if parent else None,
        "subdirs": subdirs,
    }


# ===== 机型映射表（M5：界面上给未知设备命名）=====

@app.get("/api/models")
def get_models() -> dict:
    return {
        "path": str(models_store.models_file()),
        "entries": models_store.list_entries(),
        "unknown_label": models_store.unknown_folder(),
        "default_unknown_label": models_store.device_mod.UNKNOWN_DEVICE,
    }


@app.post("/api/models")
def post_model(req: ModelRequest) -> dict:
    try:
        entry = models_store.upsert_entry(
            exif_name=req.exif_name,
            folder_name=req.folder_name,
            owner=req.owner,
            color=req.color,
        )
    except models_store.ModelValidationError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(500, f"写入映射表失败: {e}")
    return {"ok": True, "entry": entry}


@app.post("/api/models/unknown")
def post_unknown_folder(req: UnknownFolderRequest) -> dict:
    """指定「读不出机型」那批文件的归属文件夹（M5）。"""
    try:
        result = models_store.set_unknown_folder(req.folder_name)
    except models_store.ModelValidationError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(500, f"写入映射表失败: {e}")
    return {"ok": True, **result}


# ===== 扫描 =====

@app.post("/api/scans")
def create_scan(req: ScanRequest) -> dict:
    input_dir = Path(req.input_dir).expanduser()
    output_dir = Path(req.output_dir).expanduser()

    if not input_dir.is_dir():
        raise HTTPException(400, f"源目录不存在或不是目录: {input_dir}")

    # 目录关系校验在这里先做一次，省得启动了任务才发现关系非法。
    # 真正的把关在 scan.py（生成计划时）和 apply.py（执行前）各一次。
    sys.path.insert(0, str(PROJECT_ROOT))
    from modules.classify.planner import PathConflict, validate_dirs  # noqa: PLC0415

    try:
        validate_dirs(input_dir, output_dir)
    except PathConflict as e:
        raise HTTPException(400, str(e))

    job = jobs.create("scan")
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    plan_path = PLANS_DIR / f"{job.id}.json"

    threading.Thread(
        target=runner.run_scan,
        args=(job, str(input_dir), str(output_dir), plan_path, req.jobs),
        daemon=True,
    ).start()

    return {"job_id": job.id, "plan_id": job.id}


@app.post("/api/plans/{plan_id}/apply")
def apply_plan(plan_id: str, req: ApplyRequest) -> dict:
    plan_path = PLANS_DIR / f"{plan_id}.json"
    if not plan_path.is_file():
        raise HTTPException(404, "计划不存在，可能已被清理或从未生成")

    job = jobs.create("apply")
    threading.Thread(
        target=runner.run_apply,
        args=(job, plan_path, req.dry_run),
        daemon=True,
    ).start()
    return {"job_id": job.id}


# ===== 任务与进度 =====

@app.get("/api/jobs")
def list_jobs() -> dict:
    return {"jobs": jobs.list()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在（服务重启后内存中的任务会丢失）")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str) -> StreamingResponse:
    """SSE 进度流。断线重连会从头补发，不会漏事件。"""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")

    def stream() -> Iterator[str]:
        index = 0
        while True:
            events, finished = job.events_since(index, timeout=15.0)
            for event in events:
                kind = event.get("kind", "message")
                payload = {k: v for k, v in event.items() if k != "kind"}
                yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            index += len(events)

            if finished and index >= len(job._events):      # noqa: SLF001 — 同模块内的收尾判断
                break
            if not events:
                yield ": keepalive\n\n"                     # 让代理别掐断空闲连接

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ===== 计划 =====

def _load_plan(plan_id: str) -> dict:
    plan_path = PLANS_DIR / f"{plan_id}.json"
    if not plan_path.is_file():
        raise HTTPException(404, "计划不存在")
    try:
        return json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, f"计划文件损坏: {e}")


@app.get("/api/plans/{plan_id}")
def get_plan(
    plan_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(PAGE_DEFAULT, ge=1, le=PAGE_MAX),
    device: Optional[str] = None,
    kind: Optional[str] = None,
) -> dict:
    """分页取计划条目。可按设备型号、类型过滤，便于界面上按需查看。"""
    plan = _load_plan(plan_id)
    entries = plan.get("entries", [])

    if device:
        entries = [e for e in entries if e.get("device") == device]
    if kind:
        entries = [e for e in entries if e.get("kind") == kind]

    total = len(entries)
    return {
        "plan_id": plan_id,
        "created_at": plan.get("created_at"),
        "input_dir": plan.get("input_dir"),
        "output_dir": plan.get("output_dir"),
        "summary": plan.get("summary", {}),
        "skipped": plan.get("skipped", [])[:500],
        "total": total,
        "offset": offset,
        "limit": limit,
        "entries": entries[offset:offset + limit],
    }


# ===== 前端静态文件 =====

if (FRONTEND_DIST / "index.html").is_file():
    # 构建产物在前端路由需要回落到 index.html，所以 SPA 那套要自己处理
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")

    @app.get("/{path:path}")
    def spa_fallback(path: str) -> FileResponse:
        candidate = FRONTEND_DIST / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    def not_built() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": "前端尚未构建", "hint": "在 web/frontend 下执行 npm install && npm run build"},
        )
