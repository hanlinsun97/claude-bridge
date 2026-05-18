import os
import json
import tempfile
import fcntl
from contextlib import contextmanager
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


def _lock_path() -> Path:
    return _home() / "queue.lock"


@contextmanager
def _locked_queue():
    path = _lock_path()
    with path.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def load() -> Queue:
    with _locked_queue():
        return _load_unlocked()


def _load_unlocked() -> Queue:
    path = _queue_path()
    if not path.exists():
        return Queue()
    return Queue.from_json(path.read_text())


def _save(queue: Queue) -> None:
    # Must be called while holding _locked_queue(); writers above all do.
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
    with _locked_queue():
        queue = _load_unlocked()
        if front:
            queue.jobs.insert(0, job)
        else:
            queue.jobs.append(job)
        _save(queue)


def update(job_id: str, **kwargs) -> None:
    with _locked_queue():
        queue = _load_unlocked()
        for job in queue.jobs:
            if job.id == job_id:
                for k, v in kwargs.items():
                    setattr(job, k, v)
                break
        _save(queue)


def remove(job_id: str) -> None:
    with _locked_queue():
        queue = _load_unlocked()
        queue.jobs = [j for j in queue.jobs if j.id != job_id]
        _save(queue)


def clear_pending() -> None:
    with _locked_queue():
        queue = _load_unlocked()
        queue.jobs = [j for j in queue.jobs if j.status != "pending"]
        _save(queue)


def next_pending() -> Optional[Job]:
    with _locked_queue():
        queue = _load_unlocked()
    for job in queue.jobs:
        if job.status == "pending":
            return job
    return None
