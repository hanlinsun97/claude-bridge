import json
import shutil
import difflib
from pathlib import Path
from claude_bridge.queue import _home


def workspace_path(job_id: str) -> Path:
    return _home() / "workspaces" / job_id


def create(job_id: str, cwd: str, source_files: list[str]) -> str:
    ws = workspace_path(job_id)
    ws.mkdir(parents=True, exist_ok=True)
    src = Path(cwd)

    for pattern in source_files:
        src_item = src / pattern.rstrip("/")
        dst_item = ws / pattern.rstrip("/")
        if src_item.is_dir():
            shutil.copytree(src_item, dst_item, dirs_exist_ok=True)
        elif src_item.is_file():
            dst_item.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_item, dst_item)

    settings_dir = ws / ".claude"
    settings_dir.mkdir(exist_ok=True)
    settings = {
        "permissions": {
            "allow": ["Bash(*)", "Read(*)", "Write(*)", "Edit(*)"],
            "deny": [],
        }
    }
    (settings_dir / "settings.json").write_text(json.dumps(settings, indent=2))

    return str(ws)


def diff(job_id: str, cwd: str) -> str:
    ws = workspace_path(job_id)
    src = Path(cwd)
    lines = []

    for ws_file in sorted(ws.rglob("*")):
        if not ws_file.is_file():
            continue
        rel = ws_file.relative_to(ws)
        if str(rel).startswith(".claude"):
            continue
        orig = src / rel
        ws_text = ws_file.read_text(errors="replace").splitlines(keepends=True)
        orig_text = orig.read_text(errors="replace").splitlines(keepends=True) if orig.exists() else []
        chunk = list(difflib.unified_diff(orig_text, ws_text, fromfile=str(orig), tofile=str(ws_file)))
        lines.extend(chunk)

    return "".join(lines)


def apply(job_id: str, cwd: str) -> None:
    ws = workspace_path(job_id)
    src = Path(cwd)

    for ws_file in sorted(ws.rglob("*")):
        if not ws_file.is_file():
            continue
        rel = ws_file.relative_to(ws)
        if str(rel).startswith(".claude"):
            continue
        dest = src / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ws_file, dest)


def discard(job_id: str) -> None:
    ws = workspace_path(job_id)
    if ws.exists():
        shutil.rmtree(ws)
