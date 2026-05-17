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
    assert "claude-bridge" in " ".join(data["ProgramArguments"])

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
        with patch("claude_bridge.daemon._run_job", return_value=True) as mock_run:
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
