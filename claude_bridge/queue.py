import os
import json
import tempfile
from pathlib import Path
from typing import Optional
from claude_bridge.models import Job, Queue


def _home() -> Path:
    base = os.environ.get("CLAUDE_BRIDGE_HOME", str(Path.home() / ".claude-bridge"))
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _queue_path() -> Path:
    return _home() / "queue.json"


def load() -> Queue:
    path = _queue_path()
    if not path.exists():
        return Queue()
    return Queue.from_json(path.read_text())


def _save(queue: Queue) -> None:
    path = _queue_path()
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(queue.to_json())
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise


def add(job: Job, front: bool = False) -> None:
    queue = load()
    if front:
        queue.jobs.insert(0, job)
    else:
        queue.jobs.append(job)
    _save(queue)


def update(job_id: str, **kwargs) -> None:
    queue = load()
    for job in queue.jobs:
        if job.id == job_id:
            for k, v in kwargs.items():
                setattr(job, k, v)
            break
    _save(queue)


def remove(job_id: str) -> None:
    queue = load()
    queue.jobs = [j for j in queue.jobs if j.id != job_id]
    _save(queue)


def clear_pending() -> None:
    queue = load()
    queue.jobs = [j for j in queue.jobs if j.status != "pending"]
    _save(queue)


def next_pending() -> Optional[Job]:
    queue = load()
    for job in queue.jobs:
        if job.status == "pending":
            return job
    return None
