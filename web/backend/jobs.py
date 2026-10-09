#!/usr/bin/env python3.11
"""
jobs.py — 任务登记与事件流

一次扫描几千个文件要跑几分钟，浏览器不能干等着。所以每个任务是一个
可订阅的事件流：后台线程往里塞事件，SSE 端点按序取走推给前端。

单用户本机服务，状态放内存就够；重活（计划）落盘，见 app.py。
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 事件类型
EV_PROGRESS = "progress"   # {done, total}
EV_LOG = "log"             # {text}
EV_RESULT = "result"       # [RESULT] 解析后的键值
EV_DONE = "done"           # {state, ok}
EV_ERROR = "error"         # {text}

# 内存里保留的日志尾部行数，避免长任务把内存吃满
LOG_TAIL_MAX = 400


@dataclass
class Job:
    id: str
    kind: str                       # scan | apply
    state: str = "running"          # running | done | failed
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    progress: Dict[str, int] = field(default_factory=lambda: {"done": 0, "total": 0})
    result: Dict[str, str] = field(default_factory=dict)
    error: str = ""
    log_tail: List[str] = field(default_factory=list)
    plan_path: Optional[str] = None

    # 事件流。列表 + 条件变量：订阅者可以从头补发，也可以只等新事件
    _events: List[dict] = field(default_factory=list)
    _cond: threading.Condition = field(default_factory=threading.Condition)

    def emit(self, kind: str, **payload) -> None:
        with self._cond:
            self._events.append({"kind": kind, **payload})
            self._cond.notify_all()

    def log(self, text: str) -> None:
        """记录一行日志，同时作为事件推给订阅者。"""
        line = text.rstrip("\n")
        with self._cond:
            self.log_tail.append(line)
            if len(self.log_tail) > LOG_TAIL_MAX:
                del self.log_tail[:-LOG_TAIL_MAX]
        self.emit(EV_LOG, text=line)

    def finish(self, ok: bool, error: str = "") -> None:
        with self._cond:
            self.state = "done" if ok else "failed"
            self.error = error
            self.finished_at = time.time()
            self._events.append({"kind": EV_DONE, "state": self.state, "ok": ok})
            self._cond.notify_all()

    def events_since(self, index: int, timeout: float = 15.0):
        """等到有新事件或任务结束。返回 (新事件, 是否已结束)。"""
        with self._cond:
            if index >= len(self._events) and self.state == "running":
                self._cond.wait(timeout)
            return self._events[index:], self.state != "running"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "plan_path": self.plan_path,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, kind: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: -j.created_at)
        return [j.to_dict() for j in jobs[:50]]


def parse_result_line(line: str) -> Optional[Dict[str, str]]:
    """把 [RESULT] planned=251 skipped=4 解析成字典。"""
    if not line.startswith("[RESULT]"):
        return None
    fields: Dict[str, str] = {}
    for token in line[len("[RESULT]"):].split():
        if "=" in token:
            k, v = token.split("=", 1)
            fields[k] = v
    return fields


def parse_progress_line(line: str) -> Optional[Dict[str, int]]:
    """把 [PROGRESS] done=100 total=251 解析成字典。"""
    if not line.startswith("[PROGRESS]"):
        return None
    out: Dict[str, int] = {}
    for token in line[len("[PROGRESS]"):].split():
        if "=" in token:
            k, v = token.split("=", 1)
            try:
                out[k] = int(v)
            except ValueError:
                pass
    return out or None
