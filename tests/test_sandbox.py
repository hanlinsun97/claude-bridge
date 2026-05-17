# tests/test_sandbox.py
import os
from pathlib import Path
import pytest
from claude_bridge import sandbox

def test_create_workspace_copies_files(bridge_home, tmp_path):
    src = tmp_path / "project"
    src.mkdir()
    (src / "main.py").write_text("print('hello')")
    (src / "lib").mkdir()
    (src / "lib" / "util.py").write_text("def helper(): pass")

    ws = sandbox.create(job_id="job-1", cwd=str(src), source_files=["main.py", "lib/"])
    assert Path(ws, "main.py").read_text() == "print('hello')"
    assert Path(ws, "lib", "util.py").exists()

def test_create_workspace_writes_settings_json(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "f.py").write_text("x=1")
    ws = sandbox.create(job_id="job-2", cwd=str(src), source_files=["f.py"])
    settings = Path(ws, ".claude", "settings.json")
    assert settings.exists()
    import json
    data = json.loads(settings.read_text())
    assert data.get("permissions", {}).get("allow") is not None

def test_diff_detects_changes(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "main.py").write_text("original")
    ws = sandbox.create(job_id="job-3", cwd=str(src), source_files=["main.py"])
    Path(ws, "main.py").write_text("modified")
    diff = sandbox.diff(job_id="job-3", cwd=str(src))
    assert "original" in diff
    assert "modified" in diff

def test_diff_empty_when_no_changes(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "main.py").write_text("same")
    ws = sandbox.create(job_id="job-4", cwd=str(src), source_files=["main.py"])
    diff = sandbox.diff(job_id="job-4", cwd=str(src))
    assert diff.strip() == ""

def test_apply_copies_changes_back(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "main.py").write_text("original")
    ws = sandbox.create(job_id="job-5", cwd=str(src), source_files=["main.py"])
    Path(ws, "main.py").write_text("modified")
    sandbox.apply(job_id="job-5", cwd=str(src))
    assert (src / "main.py").read_text() == "modified"

def test_discard_removes_workspace(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "f.py").write_text("x")
    ws = sandbox.create(job_id="job-6", cwd=str(src), source_files=["f.py"])
    sandbox.discard(job_id="job-6")
    assert not Path(ws).exists()

def test_workspace_path(bridge_home):
    ws = sandbox.workspace_path("my-job")
    assert "my-job" in str(ws)
