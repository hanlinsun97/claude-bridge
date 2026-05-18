# tests/test_sandbox.py
import json
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

def test_create_workspace_writes_source_manifest(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "f.py").write_text("x=1")
    ws = sandbox.create(job_id="job-manifest", cwd=str(src), source_files=["f.py"])
    manifest = Path(ws, ".claude", "source_files.json")
    assert json.loads(manifest.read_text()) == ["f.py"]

def test_create_rejects_paths_outside_cwd(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    with pytest.raises(sandbox.SandboxError):
        sandbox.create(job_id="job-bad-path", cwd=str(src), source_files=["../secret.py"])

def test_create_rejects_missing_source_files(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    with pytest.raises(sandbox.SandboxError):
        sandbox.create(job_id="job-missing", cwd=str(src), source_files=["missing.py"])

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

def test_diff_detects_deleted_workspace_file(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "main.py").write_text("original\n")
    ws = sandbox.create(job_id="job-delete", cwd=str(src), source_files=["main.py"])
    Path(ws, "main.py").unlink()
    diff = sandbox.diff(job_id="job-delete", cwd=str(src))
    assert "--- " in diff
    assert "-original" in diff

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

def test_apply_rejects_missing_workspace(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    with pytest.raises(sandbox.SandboxError):
        sandbox.apply(job_id="missing", cwd=str(src))

def test_create_preserves_in_progress_work_on_recreate(bridge_home, tmp_path):
    """Deferred-retry case: re-calling create() must not overwrite Claude's progress."""
    src = tmp_path / "proj"
    src.mkdir()
    (src / "main.py").write_text("original\n")
    ws = sandbox.create(job_id="job-retry", cwd=str(src), source_files=["main.py"])
    Path(ws, "main.py").write_text("in-progress edit\n")
    ws2 = sandbox.create(job_id="job-retry", cwd=str(src), source_files=["main.py"])
    assert ws == ws2
    assert Path(ws, "main.py").read_text() == "in-progress edit\n"

def test_create_rejects_recreate_with_changed_source_list(bridge_home, tmp_path):
    """If source_files changed since initial create, refuse to silently reuse workspace."""
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("a")
    (src / "b.py").write_text("b")
    sandbox.create(job_id="job-changed", cwd=str(src), source_files=["a.py"])
    with pytest.raises(sandbox.SandboxError, match="workspaces discard job-changed"):
        sandbox.create(job_id="job-changed", cwd=str(src), source_files=["a.py", "b.py"])

def test_create_rejects_recreate_with_corrupt_spec(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("a")
    ws = sandbox.create(job_id="job-corrupt", cwd=str(src), source_files=["a.py"])
    Path(ws, ".claude", "source_spec.json").write_text("{not valid json")
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.create(job_id="job-corrupt", cwd=str(src), source_files=["a.py"])
    assert "corrupt source_spec" in str(exc.value)
    assert "workspaces discard job-corrupt" in str(exc.value)

def test_create_rejects_recreate_with_missing_spec(bridge_home, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("a")
    ws = sandbox.create(job_id="job-missing-spec", cwd=str(src), source_files=["a.py"])
    Path(ws, ".claude", "source_spec.json").unlink()
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.create(job_id="job-missing-spec", cwd=str(src), source_files=["a.py"])
    assert "missing its source_spec" in str(exc.value)
    assert "workspaces discard job-missing-spec" in str(exc.value)

def test_create_rejects_unregistered_files_in_workspace(bridge_home, tmp_path):
    """Workspace dir has user content but no manifest — refuse to fresh-init over it."""
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("a")
    ws = sandbox.workspace_path("job-orphan")
    ws.mkdir(parents=True)
    (ws / "stale.py").write_text("possibly valuable")
    with pytest.raises(sandbox.SandboxError, match="unregistered files"):
        sandbox.create(job_id="job-orphan", cwd=str(src), source_files=["a.py"])
