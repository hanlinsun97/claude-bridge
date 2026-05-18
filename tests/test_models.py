from claude_autoresumer.models import Job, Queue


def test_job_defaults():
    job = Job(prompt="do something", cwd="/tmp")
    assert job.status == "pending"
    assert job.type == "task"
    assert job.model == "claude-sonnet-4-6"
    assert job.max_retry_hours == 24.0
    assert job.session_id is None
    assert job.next_eligible_at is None
    assert len(job.id) == 36  # UUID


def test_job_to_dict_round_trip():
    job = Job(prompt="test", cwd="/tmp/proj", max_retry_hours=12.0)
    d = job.to_dict()
    job2 = Job.from_dict(d)
    assert job2.id == job.id
    assert job2.prompt == job.prompt
    assert job2.max_retry_hours == 12.0


def test_job_from_dict_drops_and_warns_on_legacy_fields():
    """Old queue.json may have workflow/self_healing dicts — drop them but warn loudly."""
    import warnings as _w
    legacy = {
        "prompt": "old",
        "cwd": "/tmp",
        "workflow": {"pre_skills": ["x"]},
        "self_healing": {"mode": "time_bounded", "max_hours": 8.0},
    }
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        job = Job.from_dict(legacy)
    assert job.prompt == "old"
    assert not hasattr(job, "workflow")
    assert not hasattr(job, "self_healing")
    assert any("legacy queue schema" in str(w.message) for w in caught)
    msgs = " ".join(str(w.message) for w in caught)
    assert "workflow" in msgs and "self_healing" in msgs


def test_queue_to_json_round_trip():
    q = Queue()
    job = Job(prompt="hello", cwd="/tmp")
    q.jobs.append(job)
    raw = q.to_json()
    q2 = Queue.from_json(raw)
    assert len(q2.jobs) == 1
    assert q2.jobs[0].prompt == "hello"
    assert q2.schema_version == "2.0"
