import json
import plistlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

from claude_autoresumer import daemon
from claude_autoresumer.models import Job
from claude_autoresumer import queue as q_mod


def test_plist_content(bridge_home):
    plist_str = daemon.generate_plist(bridge_home=str(bridge_home))
    data = plistlib.loads(plist_str.encode())
    assert data["Label"] == "com.claude-autoresumer"
    assert data["StartInterval"] == 600
    assert "claude_autoresumer.cli" in " ".join(data["ProgramArguments"])


def test_plist_has_log_paths(bridge_home):
    plist_str = daemon.generate_plist(bridge_home=str(bridge_home))
    data = plistlib.loads(plist_str.encode())
    assert "StandardOutPath" in data
    assert "StandardErrorPath" in data


def test_install_writes_plist(bridge_home, tmp_path):
    launch_agents = tmp_path / "LaunchAgents"
    launch_agents.mkdir()
    with patch("claude_autoresumer.daemon.LAUNCH_AGENTS_DIR", str(launch_agents)):
        with patch("claude_autoresumer.daemon.subprocess.run"):
            daemon.install(bridge_home=str(bridge_home))
    assert (launch_agents / "com.claude-autoresumer.plist").exists()


def test_tick_skips_when_usage_unavailable(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    q_mod.add(Job(prompt="p", cwd=str(src), source_files=["f.py"]))
    with patch("claude_autoresumer.daemon.probe", return_value=False):
        result = daemon.tick(bridge_home=str(bridge_home))
    assert result == "no_usage"


def test_tick_skips_when_queue_empty(bridge_home):
    with patch("claude_autoresumer.daemon.probe", return_value=True):
        result = daemon.tick(bridge_home=str(bridge_home))
    assert result == "queue_empty"


def test_install_seeds_state(bridge_home, tmp_path):
    launch_agents = tmp_path / "LaunchAgents"
    launch_agents.mkdir()
    with patch("claude_autoresumer.daemon.LAUNCH_AGENTS_DIR", str(launch_agents)):
        with patch("claude_autoresumer.daemon.subprocess.run"):
            daemon.install(bridge_home=str(bridge_home))
    state = daemon.read_state(str(bridge_home))
    assert state["armed_at"]
    assert state["tick_count"] == 0
    assert state["last_tick_at"] is None


def test_tick_records_heartbeat(bridge_home):
    with patch("claude_autoresumer.daemon.probe", return_value=True):
        daemon.tick(bridge_home=str(bridge_home))
    state = daemon.read_state(str(bridge_home))
    assert state["last_tick_at"]
    assert state["last_tick_result"] == "queue_empty"
    assert state["tick_count"] == 1


def test_tick_increments_count_across_calls(bridge_home):
    with patch("claude_autoresumer.daemon.probe", return_value=True):
        daemon.tick(bridge_home=str(bridge_home))
        daemon.tick(bridge_home=str(bridge_home))
        daemon.tick(bridge_home=str(bridge_home))
    state = daemon.read_state(str(bridge_home))
    assert state["tick_count"] == 3


def test_tick_records_result_on_exception(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    q_mod.add(Job(prompt="p", cwd=str(src), source_files=["f.py"]))
    boom = RuntimeError("boom")
    with patch("claude_autoresumer.daemon.probe", side_effect=boom):
        try:
            daemon.tick(bridge_home=str(bridge_home))
        except RuntimeError:
            pass
    state = daemon.read_state(str(bridge_home))
    # ProbeError is caught and returned as "probe_error"; a non-ProbeError
    # propagates, but the finally-clause must still record a heartbeat.
    assert state["last_tick_at"]
    assert state["tick_count"] == 1


def test_read_state_returns_empty_when_missing(bridge_home):
    # no install, no tick — state file does not exist yet
    assert daemon.read_state(str(bridge_home)) == {}


def test_tick_runs_job_when_available(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "f.py").write_text("x=1")
    job = Job(prompt="do work", cwd=str(src), source_files=["f.py"])
    q_mod.add(job)

    with patch("claude_autoresumer.daemon.probe", return_value=True):
        with patch("claude_autoresumer.daemon._run_job", return_value="done") as mock_run:
            result = daemon.tick(bridge_home=str(bridge_home))

    assert result == "ran_job"
    mock_run.assert_called_once()


def test_tick_retry_window_expired_marks_failed(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    past = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"],
              max_retry_hours=24.0, started_at=past, session_id="abc")
    q_mod.add(job)

    with patch("claude_autoresumer.daemon.probe", return_value=True):
        result = daemon.tick(bridge_home=str(bridge_home))

    assert result == "retry_window_expired"
    saved = q_mod.load().jobs[0]
    assert saved.status == "failed"
    assert "max_retry_hours" in saved.error


def test_tick_waits_for_known_reset_time(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"],
              next_eligible_at=future)
    q_mod.add(job)

    # probe should NOT be called when we know the reset hasn't happened yet
    with patch("claude_autoresumer.daemon.probe", side_effect=AssertionError("should not be called")):
        result = daemon.tick(bridge_home=str(bridge_home))

    assert result == "waiting_for_reset"


def test_tick_proceeds_when_eligible_time_has_passed(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"],
              next_eligible_at=past)
    q_mod.add(job)

    with patch("claude_autoresumer.daemon.probe", return_value=True):
        with patch("claude_autoresumer.daemon._run_job", return_value="done"):
            result = daemon.tick(bridge_home=str(bridge_home))

    assert result == "ran_job"


def test_run_job_passes_model_to_claude(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"], model="claude-opus-4-7")
    q_mod.add(job)

    result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("claude_autoresumer.daemon.subprocess.run", return_value=result) as mock_run:
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

    with patch("claude_autoresumer.daemon.subprocess.run", side_effect=OSError("boom")):
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "failed"

    saved = q_mod.load().jobs[0]
    assert saved.status == "failed"
    assert "boom" in saved.error


def test_run_job_defers_usage_limit_failure(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"])
    q_mod.add(job)

    result = MagicMock(returncode=1, stdout="", stderr="usage limit reached")
    with patch("claude_autoresumer.daemon.subprocess.run", return_value=result):
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "deferred"

    saved = q_mod.load().jobs[0]
    assert saved.status == "pending"
    assert "usage limit" in saved.error


def test_run_job_does_not_defer_when_retry_window_expired(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    past = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"],
              max_retry_hours=24.0, started_at=past, session_id="abc")
    q_mod.add(job)

    result = MagicMock(returncode=1, stdout="", stderr="usage limit reached")
    with patch("claude_autoresumer.daemon.subprocess.run", return_value=result):
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "failed"


def test_run_job_persists_next_eligible_at_when_reset_time_parseable(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"])
    q_mod.add(job)

    future_epoch = int((datetime.now(timezone.utc) + timedelta(hours=3)).timestamp())
    err = f"Claude AI usage limit reached|{future_epoch}"
    result = MagicMock(returncode=1, stdout="", stderr=err)
    with patch("claude_autoresumer.daemon.subprocess.run", return_value=result):
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "deferred"

    saved = q_mod.load().jobs[0]
    assert saved.next_eligible_at is not None
    parsed = datetime.fromisoformat(saved.next_eligible_at)
    expected = datetime.fromtimestamp(future_epoch, tz=timezone.utc)
    assert abs((parsed - expected).total_seconds()) < 2


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
    with patch("claude_autoresumer.daemon.subprocess.run", return_value=result) as mock_run:
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "done"

    args = mock_run.call_args[0][0]
    assert "--session-id" in args
    sid_index = args.index("--session-id")
    assigned_sid = args[sid_index + 1]
    import uuid as _u
    _u.UUID(assigned_sid)
    saved = q_mod.load().jobs[0]
    assert saved.session_id == assigned_sid


def test_run_job_retry_uses_resume_with_stored_session_id(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="original task", cwd=str(src), source_files=["f.py"],
              session_id="11111111-2222-3333-4444-555555555555")
    q_mod.add(job)

    result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("claude_autoresumer.daemon.subprocess.run", return_value=result) as mock_run:
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "done"

    args = mock_run.call_args[0][0]
    assert "--resume" in args
    assert "11111111-2222-3333-4444-555555555555" in args
    assert "--session-id" not in args
    assert "original task" not in args
    assert args[-1] == daemon.RESUME_PROMPT


def test_tick_retry_window_check_runs_before_wait_for_reset(bridge_home, tmp_path):
    """A bogus far-future next_eligible_at must NOT park the job past its retry window."""
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    far_future = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    past_start = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"],
              max_retry_hours=24.0, started_at=past_start, session_id="abc",
              next_eligible_at=far_future)
    q_mod.add(job)

    with patch("claude_autoresumer.daemon.probe", side_effect=AssertionError("should not probe")):
        result = daemon.tick(bridge_home=str(bridge_home))

    assert result == "retry_window_expired"
    saved = q_mod.load().jobs[0]
    assert saved.status == "failed"


def test_run_job_caps_absurd_reset_timestamp(bridge_home, tmp_path, capfd):
    """parse_reset_at can return a year-2099 value if claude lies; daemon should drop it."""
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"])
    q_mod.add(job)

    far_future_epoch = int((datetime.now(timezone.utc) + timedelta(days=365)).timestamp())
    err = f"Claude AI usage limit reached|{far_future_epoch}"
    result = MagicMock(returncode=1, stdout="", stderr=err)
    with patch("claude_autoresumer.daemon.subprocess.run", return_value=result):
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "deferred"

    saved = q_mod.load().jobs[0]
    # The far-future timestamp must NOT be persisted — we'd never tick again.
    assert saved.next_eligible_at is None
    captured = capfd.readouterr()
    assert "dropping reset timestamp" in captured.err


def test_run_job_accepts_weekly_limit_reset_within_horizon(bridge_home, tmp_path):
    """A 7-day weekly-limit reset must be preserved (not dropped by the sanity cap)."""
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"],
              max_retry_hours=24 * 8.0)  # match the horizon
    q_mod.add(job)

    weekly_epoch = int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp())
    err = f"Claude AI usage limit reached|{weekly_epoch}"
    result = MagicMock(returncode=1, stdout="", stderr=err)
    with patch("claude_autoresumer.daemon.subprocess.run", return_value=result):
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "deferred"

    saved = q_mod.load().jobs[0]
    assert saved.next_eligible_at is not None
    parsed = datetime.fromisoformat(saved.next_eligible_at)
    expected = datetime.fromtimestamp(weekly_epoch, tz=timezone.utc)
    assert abs((parsed - expected).total_seconds()) < 2


def test_legacy_queue_schema_warns_on_load(bridge_home):
    """Loading a queue.json with old workflow/self_healing fields should warn."""
    import warnings as _w
    legacy_queue = {
        "schema_version": "1.0",
        "jobs": [{
            "id": "12345678-aaaa-bbbb-cccc-dddddddddddd",
            "prompt": "old job",
            "cwd": "/tmp",
            "workflow": {"pre_skills": []},
            "self_healing": {"mode": "always", "max_hours": 8.0},
        }],
    }
    (bridge_home / "queue.json").write_text(json.dumps(legacy_queue))
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        q_mod.load()
    assert any("legacy queue schema" in str(w.message) for w in caught)


def test_run_job_deferred_then_retry_preserves_session_id(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    job = Job(prompt="work", cwd=str(src), source_files=["f.py"])
    q_mod.add(job)

    defer_result = MagicMock(returncode=1, stdout="", stderr="usage limit reached")
    with patch("claude_autoresumer.daemon.subprocess.run", return_value=defer_result) as mock_run:
        assert daemon._run_job(job, bridge_home=str(bridge_home)) == "deferred"
    first_args = mock_run.call_args[0][0]
    assigned_sid = first_args[first_args.index("--session-id") + 1]

    refreshed = q_mod.load().jobs[0]
    assert refreshed.session_id == assigned_sid
    assert refreshed.status == "pending"

    ok_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("claude_autoresumer.daemon.subprocess.run", return_value=ok_result) as mock_run:
        assert daemon._run_job(refreshed, bridge_home=str(bridge_home)) == "done"
    retry_args = mock_run.call_args[0][0]
    assert "--resume" in retry_args
    assert assigned_sid in retry_args
    assert "--session-id" not in retry_args
