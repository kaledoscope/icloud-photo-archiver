#!/usr/bin/env python3.11
"""
runner.py — 用 subprocess 调 modules.classify 的 CLI

网页后端不 import 分类模块，而是像 photo_backup_weekly.sh 一样起子进程、
读 [PROGRESS] / [RESULT] 行。这样 web/ 整个目录删掉，命令行系统照常工作
（宪法铁律 Ⅱ：删掉任一模块，其余模块仍独立运作）。

子进程隔离还带来两个好处：
  - 扫描崩溃不会带崩 web 服务
  - 扫描是 CPU/IO 密集的，跑在独立进程里不抢服务端的 GIL
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from jobs import Job, parse_progress_line, parse_result_line

# web/backend/runner.py → 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 起子进程时限制并发，避免把机器读死
DEFAULT_JOBS = 8


def python_bin() -> str:
    """跑分类模块用哪个解释器。

    优先项目自带虚拟环境；它只是 web 层的依赖容器，分类模块本身
    只用标准库，所以换成系统 python 也照跑。
    """
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return str(venv_python)
    return sys.executable


def _env() -> dict:
    env = dict(os.environ)
    # 模块用 `python -m modules.classify.scan` 调用，需要项目根在 sys.path 上
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{PROJECT_ROOT}{os.pathsep}{existing}" if existing else str(PROJECT_ROOT)
    # 子进程输出立刻可见，进度才流得动
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _pump(job: Job, cmd: List[str]) -> bool:
    """起子进程，逐行读输出并转成事件。返回是否成功退出。

    stderr 合并进 stdout：出错信息也是给用户看的日志的一部分。
    """
    job.log(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,          # 行缓冲
        )
    except OSError as e:
        job.log(f"❌ 无法启动子进程: {e}")
        return False

    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n")

            progress = parse_progress_line(line)
            if progress is not None:
                job.progress.update(progress)
                job.emit("progress", **job.progress)
                continue

            result = parse_result_line(line)
            if result is not None:
                job.result = result
                job.emit("result", **result)
                continue

            # 其余都是给人看的日志。空行不必推给前端。
            if line.strip():
                job.log(line)
    except Exception as e:                      # noqa: BLE001 — 读流失败也要让任务收尾
        job.log(f"❌ 读取子进程输出失败: {e}")
        proc.kill()
        proc.wait()
        return False

    code = proc.wait()
    return code == 0


def run_scan(
    job: Job,
    input_dir: str,
    output_dir: str,
    plan_path: Path,
    jobs: int = DEFAULT_JOBS,
) -> None:
    """扫描 → 写计划 JSON。这个方法在后台线程里跑。"""
    cmd = [
        python_bin(), "-u", "-m", "modules.classify.scan",
        "--input", input_dir,
        "--output", output_dir,
        "--plan-out", str(plan_path),
        "--jobs", str(jobs),
    ]
    try:
        ok = _pump(job, cmd)
    except Exception as e:                      # noqa: BLE001
        job.finish(False, f"扫描异常: {e}")
        return

    if ok and plan_path.is_file():
        job.plan_path = str(plan_path)
        job.finish(True)
    elif ok:
        job.finish(False, "扫描结束但没有产出计划文件")
    else:
        job.finish(False, "扫描失败，详见日志")


def run_apply(job: Job, plan_path: Path, dry_run: bool = False) -> None:
    """按计划复制。这个方法在后台线程里跑。"""
    cmd = [python_bin(), "-u", "-m", "modules.classify.apply", "--plan", str(plan_path)]
    if dry_run:
        cmd.append("--dry-run")
    try:
        ok = _pump(job, cmd)
    except Exception as e:                      # noqa: BLE001
        job.finish(False, f"复制异常: {e}")
        return
    job.finish(ok, "" if ok else "复制过程中有失败项，详见日志")


def resolve_python_bin() -> Optional[str]:
    """给 /api/health 用：报告后端会拿哪个解释器跑分类。"""
    return python_bin()
