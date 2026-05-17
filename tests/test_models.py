import json
from claude_bridge.models import Job, WorkflowConfig, SelfHealingConfig, Queue

def test_job_defaults():
    job = Job(prompt="do something", cwd="/tmp")
    assert job.status == "pending"
    assert job.type == "task"
    assert job.model == "claude-sonnet-4-6"
    assert len(job.id) == 36  # UUID

def test_workflow_config_defaults():
    wf = WorkflowConfig()
    assert wf.pre_skills == []
    assert wf.codex_iterations == 0
    assert wf.validation is None

def test_self_healing_defaults():
    sh = SelfHealingConfig()
    assert sh.mode == "time_bounded"
    assert sh.max_hours == 8.0
    assert sh.max_resets is None

def test_job_to_dict_round_trip():
    job = Job(prompt="test", cwd="/tmp/proj")
    d = job.to_dict()
    job2 = Job.from_dict(d)
    assert job2.id == job.id
    assert job2.prompt == job.prompt
    assert job2.workflow.pre_skills == []

def test_queue_to_json_round_trip():
    q = Queue()
    job = Job(prompt="hello", cwd="/tmp")
    q.jobs.append(job)
    raw = q.to_json()
    q2 = Queue.from_json(raw)
    assert len(q2.jobs) == 1
    assert q2.jobs[0].prompt == "hello"
    assert q2.schema_version == "1.0"
