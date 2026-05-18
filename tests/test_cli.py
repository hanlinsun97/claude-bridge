import json
from pathlib import Path
from click.testing import CliRunner
from claude_bridge.cli import cli
from claude_bridge import queue as q_mod
from claude_bridge import sandbox


def test_queue_add_basic(bridge_home):
    (bridge_home / "src").mkdir()
    runner = CliRunner()
    result = runner.invoke(cli, [
        "queue", "add",
        "--prompt", "do something",
        "--cwd", str(bridge_home),
        "--files", "src/",
        "--workflow", "minimal",
        "--no-self-heal",
    ])
    assert result.exit_code == 0, result.output
    queue = q_mod.load()
    assert len(queue.jobs) == 1
    assert queue.jobs[0].prompt == "do something"


def test_queue_add_accepts_repeated_file_options(bridge_home):
    (bridge_home / "src").mkdir()
    (bridge_home / "test.py").write_text("x")
    runner = CliRunner()
    result = runner.invoke(cli, [
        "queue", "add",
        "--prompt", "do something",
        "--cwd", str(bridge_home),
        "--file", "src/",
        "--file", "test.py",
    ])
    assert result.exit_code == 0, result.output
    job = q_mod.load().jobs[0]
    assert job.source_files == ["src/", "test.py"]


def test_queue_add_rejects_paths_outside_cwd(bridge_home):
    runner = CliRunner()
    result = runner.invoke(cli, [
        "queue", "add",
        "--prompt", "do something",
        "--cwd", str(bridge_home),
        "--files", "../secret.py",
    ])
    assert result.exit_code != 0
    assert "must stay inside cwd" in result.output


def test_queue_list_shows_jobs(bridge_home):
    from claude_bridge.models import Job
    q_mod.add(Job(prompt="list me", cwd="/tmp"))
    runner = CliRunner()
    result = runner.invoke(cli, ["queue", "list"])
    assert "list me" in result.output


def test_queue_clear_removes_pending(bridge_home):
    from claude_bridge.models import Job
    q_mod.add(Job(prompt="clear me", cwd="/tmp"))
    runner = CliRunner()
    runner.invoke(cli, ["queue", "clear"])
    assert q_mod.load().jobs == []


def test_queue_add_resume_reads_checkpoint(bridge_home, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({
        "prompt": "resume this",
        "cwd": str(tmp_path),
        "source_files": ["src/"],
        "model": "claude-opus-4-7",
    }))
    runner = CliRunner()
    result = runner.invoke(cli, ["queue", "add", "--resume", "--checkpoint", str(checkpoint)])
    assert result.exit_code == 0, result.output
    queue = q_mod.load()
    assert queue.jobs[0].type == "resume"
    assert "resume this" in queue.jobs[0].prompt


def test_status_shows_queue_summary(bridge_home):
    from claude_bridge.models import Job
    q_mod.add(Job(prompt="p1", cwd="/tmp"))
    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert "pending" in result.output.lower() or "1" in result.output


def test_probe_command_exits_0_when_available(bridge_home):
    from unittest.mock import patch
    runner = CliRunner()
    with patch("claude_bridge.cli._probe_fn", return_value=True):
        result = runner.invoke(cli, ["probe"])
    assert result.exit_code == 0


def test_workspaces_list_empty(bridge_home):
    runner = CliRunner()
    result = runner.invoke(cli, ["workspaces", "list"])
    assert result.exit_code == 0


def test_parse_self_heal_raises_on_bad_input():
    import click
    import pytest
    from claude_bridge.cli import _parse_self_heal
    with pytest.raises(click.BadParameter):
        _parse_self_heal("foo")


def test_start_command_has_no_self_heal_option(bridge_home):
    """The start command should not accept --self-heal."""
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--self-heal", "8h"])
    assert result.exit_code != 0


def test_discard_command(bridge_home, tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "f.py").write_text("x")
    sandbox.create("my-job", str(src), ["f.py"])
    runner = CliRunner()
    result = runner.invoke(cli, ["workspaces", "discard", "my-job"])
    assert result.exit_code == 0
    assert not sandbox.workspace_path("my-job").exists()
