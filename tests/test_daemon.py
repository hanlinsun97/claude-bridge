import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from claude_bridge import daemon
from claude_bridge.models import Job, SelfHealingConfig
from claude_bridge import queue as q_mod
import plistlib

def test_plist_content(bridge_home):
    plist_str = daemon.generate_plist(bridge_home=str(bridge_home))
    data = plistlib.loads(plist_str.encode())
    assert data["Label"] == "com.claude-bridge"
    assert data["StartInterval"] == 600
    assert "claude_bridge.cli" in " ".join(data["ProgramArguments"])

def test_plist_has_log_paths(bridge_home):
    plist_str = daemon.generate_plist(bridge_home=str(bridge_home))
    data = plistlib.loads(plist_str.encode())
    assert "StandardOutPath" in data
    assert "StandardErrorPath" in data

def test_install_writes_plist(bridge_home, tmp_path):
    launch_agents = tmp_path / "LaunchAgents"
    launch_agents.mkdir()
    with patch("claude_bridge.daemon.LAUNCH_AGENTS_DIR", str(launch_agents)):
        with patch("claude_bridge.daemon.subprocess.run"):
            daemon.install(bridge_home=str(bridge_home))
    assert (launch_agents / "com.claude-bridge.plist").exists()

def test_tick_skips_when_usage_unavailable(bridge_home):
    with patch("claude_bridge.daemon.probe", return_value=False):
        result = daemon.tick(bridge_home=str(bridge_home))
    assert result == "no_usage"

def test_tick_skips_when_queue_empty(bridge_home):
    with patch("claude_bridge.daemon.probe", return_value=True):
        result = daemon.tick(bridge_home=str(bridge_home))
    assert result == "queue_empty"

def test_tick_runs_job_when_available(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "f.py").write_text("x=1")

    job = Job(prompt="do work", cwd=str(src), source_files=["f.py"])
    q_mod.add(job)

    with patch("claude_bridge.daemon.probe", return_value=True):
        with patch("claude_bridge.daemon._run_job", return_value="done") as mock_run:
            result = daemon.tick(bridge_home=str(bridge_home))

    assert result == "ran_job"
    mock_run.assert_called_once()

def test_tick_respects_time_bounded_policy_expired(bridge_home, tmp_path):
    from datetime import datetime, timezone, timedelta

    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")

    job = Job(
        prompt="work",
        cwd=str(src),
        source_files=["f.py"],
        self_healing=SelfHealingConfig(mode="time_bounded", max_hours=0.0),
    )
    q_mod.add(job)

    start_file = Path(bridge_home) / "daemon_started_at.txt"
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    start_file.write_text(past.isoformat())

    with patch("claude_bridge.daemon.probe", return_value=True):
        result = daemon.tick(bridge_home=str(bridge_home))

    assert result == "policy_expired"

def test_install_clears_stale_counter_files(bridge_home, tmp_path):
    """install() should delete daemon_started_at.txt and reset_count.txt if present."""
    launch_agents = tmp_path / "LaunchAgents"
    launch_agents.mkdir()
    # Write stale counter files
    (bridge_home / "daemon_started_at.txt").write_text("2020-01-01T00:00:00+00:00")
    (bridge_home / "reset_count.txt").write_text("5")
    with patch("claude_bridge.daemon.LAUNCH_AGENTS_DIR", str(launch_agents)):
        with patch("claude_bridge.daemon.subprocess.run"):
            daemon.install(bridge_home=str(bridge_home))
    assert not (bridge_home / "daemon_started_at.txt").exists()
    assert not (bridge_home / "reset_count.txt").exists()


def test_tick_respects_max_resets(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")

    job = Job(
        prompt="work",
        cwd=str(src),
        source_files=["f.py"],
        self_healing=SelfHealingConfig(mode="time_bounded", max_resets=1),
    )
    q_mod.add(job)

    reset_file = Path(bridge_home) / "reset_count.txt"
    reset_file.write_text("1")

    with patch("claude_bridge.daemon.probe", return_value=True):
        result = daemon.tick(bridge_home=str(bridge_home))

    assert result == "policy_expired"


def test_run_job_passes_model_to_claude(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"], model="claude-opus-4-7")
    q_mod.add(job)

    result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("claude_bridge.daemon.subprocess.run", return_value=result) as mock_run:
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "done"

    args = mock_run.call_args[0][0]
    assert "--model" in args
    assert "claude-opus-4-7" in args


def test_run_job_marks_failed_on_unexpected_exception(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"])
    q_mod.add(job)

    with patch("claude_bridge.daemon.subprocess.run", side_effect=OSError("boom")):
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "failed"

    saved = q_mod.load().jobs[0]
    assert saved.status == "failed"
    assert "boom" in saved.error


def test_run_job_defers_usage_limit_failure_when_self_healing(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"])
    q_mod.add(job)

    result = MagicMock(returncode=1, stdout="", stderr="usage limit reached")
    with patch("claude_bridge.daemon.subprocess.run", return_value=result):
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "deferred"

    saved = q_mod.load().jobs[0]
    assert saved.status == "pending"
    assert "usage limit" in saved.error


def test_run_job_does_not_defer_usage_limit_for_single_session(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(
        prompt="work",
        cwd=str(src),
        source_files=["f.py"],
        self_healing=SelfHealingConfig(mode="single_session", max_hours=None, max_resets=None),
    )
    q_mod.add(job)

    result = MagicMock(returncode=1, stdout="", stderr="usage limit reached")
    with patch("claude_bridge.daemon.subprocess.run", return_value=result):
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "failed"

    saved = q_mod.load().jobs[0]
    assert saved.status == "failed"


def test_tick_does_not_burn_reset_slot_on_deferred(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"])
    q_mod.add(job)

    with patch("claude_bridge.daemon.probe", return_value=True):
        with patch("claude_bridge.daemon._run_job", return_value="deferred"):
            result = daemon.tick(bridge_home=str(bridge_home))

    assert result == "deferred_usage_limit"
    assert not (Path(bridge_home) / "reset_count.txt").exists()


def test_tick_increments_reset_count_on_completion(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"])
    q_mod.add(job)

    with patch("claude_bridge.daemon.probe", return_value=True):
        with patch("claude_bridge.daemon._run_job", return_value="done"):
            daemon.tick(bridge_home=str(bridge_home))

    assert (Path(bridge_home) / "reset_count.txt").read_text() == "1"


def test_tick_increments_reset_count_on_failure(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"])
    q_mod.add(job)

    with patch("claude_bridge.daemon.probe", return_value=True):
        with patch("claude_bridge.daemon._run_job", return_value="failed"):
            daemon.tick(bridge_home=str(bridge_home))

    assert (Path(bridge_home) / "reset_count.txt").read_text() == "1"


def test_usage_limit_detection_is_case_insensitive():
    assert daemon._usage_limit_hit("USAGE LIMIT reached")
    assert daemon._usage_limit_hit("Rate Limit exceeded")
    assert not daemon._usage_limit_hit("unrelated failure")


def test_run_job_first_run_assigns_session_id_and_uses_session_id_flag(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"])
    q_mod.add(job)

    result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("claude_bridge.daemon.subprocess.run", return_value=result) as mock_run:
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "done"

    args = mock_run.call_args[0][0]
    assert "--session-id" in args
    sid_index = args.index("--session-id")
    assigned_sid = args[sid_index + 1]
    # Looks like a UUID
    import uuid as _u
    _u.UUID(assigned_sid)
    # Persisted to the queue
    saved = q_mod.load().jobs[0]
    assert saved.session_id == assigned_sid


def test_run_job_retry_uses_resume_with_stored_session_id(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(
        prompt="original task",
        cwd=str(src),
        source_files=["f.py"],
        session_id="11111111-2222-3333-4444-555555555555",
    )
    q_mod.add(job)

    result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("claude_bridge.daemon.subprocess.run", return_value=result) as mock_run:
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "done"

    args = mock_run.call_args[0][0]
    assert "--resume" in args
    assert "11111111-2222-3333-4444-555555555555" in args
    assert "--session-id" not in args
    # Retry sends the continuation prompt, not the original
    assert "original task" not in args
    assert args[-1] == daemon.RESUME_PROMPT


def test_run_job_deferred_then_retry_preserves_session_id(bridge_home, tmp_path):
    """End-to-end: first run defers on usage limit, second run resumes same session."""
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"])
    q_mod.add(job)

    defer_result = MagicMock(returncode=1, stdout="", stderr="usage limit reached")
    with patch("claude_bridge.daemon.subprocess.run", return_value=defer_result) as mock_run:
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "deferred"
    first_args = mock_run.call_args[0][0]
    assigned_sid = first_args[first_args.index("--session-id") + 1]

    # Re-load the job from the queue (mutated state)
    refreshed = q_mod.load().jobs[0]
    assert refreshed.session_id == assigned_sid
    assert refreshed.status == "pending"

    ok_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("claude_bridge.daemon.subprocess.run", return_value=ok_result) as mock_run:
        assert daemon._run_job(refreshed, bridge_home=str(bridge_home)) == "done"
    retry_args = mock_run.call_args[0][0]
    assert "--resume" in retry_args
    assert assigned_sid in retry_args
    assert "--session-id" not in retry_args
