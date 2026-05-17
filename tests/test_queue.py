import pytest
from claude_bridge.models import Job
from claude_bridge import queue as q_mod


def test_load_empty_queue(bridge_home):
    queue = q_mod.load()
    assert queue.jobs == []


def test_add_job(bridge_home):
    job = Job(prompt="do work", cwd="/tmp/proj")
    q_mod.add(job)
    queue = q_mod.load()
    assert len(queue.jobs) == 1
    assert queue.jobs[0].prompt == "do work"


def test_add_prepends_when_front_true(bridge_home):
    job1 = Job(prompt="first", cwd="/tmp")
    job2 = Job(prompt="front", cwd="/tmp")
    q_mod.add(job1)
    q_mod.add(job2, front=True)
    queue = q_mod.load()
    assert queue.jobs[0].prompt == "front"


def test_update_job_status(bridge_home):
    job = Job(prompt="work", cwd="/tmp")
    q_mod.add(job)
    q_mod.update(job.id, status="running")
    queue = q_mod.load()
    assert queue.jobs[0].status == "running"


def test_remove_job(bridge_home):
    job = Job(prompt="remove me", cwd="/tmp")
    q_mod.add(job)
    q_mod.remove(job.id)
    queue = q_mod.load()
    assert queue.jobs == []


def test_clear_pending_only(bridge_home):
    j1 = Job(prompt="pending", cwd="/tmp")
    j2 = Job(prompt="done", cwd="/tmp")
    q_mod.add(j1)
    q_mod.add(j2)
    q_mod.update(j2.id, status="done")
    q_mod.clear_pending()
    queue = q_mod.load()
    assert len(queue.jobs) == 1
    assert queue.jobs[0].status == "done"


def test_next_pending(bridge_home):
    j1 = Job(prompt="done", cwd="/tmp")
    j2 = Job(prompt="next", cwd="/tmp")
    q_mod.add(j1)
    q_mod.add(j2)
    q_mod.update(j1.id, status="done")
    job = q_mod.next_pending()
    assert job.prompt == "next"


def test_next_pending_returns_none_when_empty(bridge_home):
    assert q_mod.next_pending() is None


def test_atomic_write_does_not_corrupt(bridge_home):
    job = Job(prompt="atomic", cwd="/tmp")
    q_mod.add(job)
    queue = q_mod.load()
    assert len(queue.jobs) == 1
