import json
import shutil
import difflib
from pathlib import Path
from claude_bridge.queue import _home


class SandboxError(Exception):
    pass


def workspace_path(job_id: str) -> Path:
    return _home() / "workspaces" / job_id


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _validate_relative_path(pattern: str) -> Path:
    clean = pattern.rstrip("/")
    rel = Path(clean)
    if not clean:
        raise SandboxError("source file path cannot be empty")
    if rel.is_absolute() or ".." in rel.parts:
        raise SandboxError(f"source file path must stay inside cwd: {pattern}")
    return rel


def create(job_id: str, cwd: str, source_files: list[str]) -> str:
    ws = workspace_path(job_id)
    ws.mkdir(parents=True, exist_ok=True)
    src = Path(cwd).resolve()
    ws_root = ws.resolve()

    if not src.exists() or not src.is_dir():
        raise SandboxError(f"cwd does not exist or is not a directory: {cwd}")

    # Validate paths up front even when we won't copy — defends against a
    # malicious queue.json mutation between deferred-retry ticks.
    for pattern in source_files:
        rel = _validate_relative_path(pattern)
        if not _is_relative_to((src / rel).resolve(), src):
            raise SandboxError(f"source file path escapes cwd: {pattern}")
        if not _is_relative_to((ws_root / rel).resolve(), ws_root):
            raise SandboxError(f"workspace destination escapes sandbox: {pattern}")

    settings_dir = ws / ".claude"
    spec_file = settings_dir / "source_spec.json"
    manifest_file = settings_dir / "source_files.json"
    already_initialized = manifest_file.exists()

    if not already_initialized:
        # If the workspace dir has any content outside .claude/ but no manifest,
        # something previously wrote files without registering them — refuse to
        # silently fresh-init over potentially-valuable bytes.
        for entry in ws.iterdir():
            if entry.name != ".claude":
                raise SandboxError(
                    f"workspace {job_id} has unregistered files but no source_files.json; "
                    f"run `claude-bridge workspaces discard {job_id}` and re-queue"
                )

    if already_initialized:
        # Workspace exists from a prior call (deferred retry). Guard against
        # the source_files list having changed since first initialization —
        # we can't safely re-sync without potentially clobbering in-progress edits.
        if not spec_file.exists():
            raise SandboxError(
                f"workspace {job_id} is missing its source_spec.json; "
                f"run `claude-bridge workspaces discard {job_id}` and re-queue"
            )
        try:
            prior_spec = json.loads(spec_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            raise SandboxError(
                f"workspace {job_id} has a corrupt source_spec.json ({e}); "
                f"run `claude-bridge workspaces discard {job_id}` and re-queue"
            ) from e
        if prior_spec != list(source_files):
            raise SandboxError(
                f"workspace {job_id} was initialized with a different source_files list; "
                f"run `claude-bridge workspaces discard {job_id}` and re-queue"
            )
    else:
        copied_files: set[str] = set()
        for pattern in source_files:
            rel = _validate_relative_path(pattern)
            src_item = (src / rel).resolve()
            dst_item = (ws_root / rel).resolve()
            if not src_item.exists():
                raise SandboxError(f"source file does not exist: {pattern}")
            if src_item.is_dir():
                shutil.copytree(src_item, dst_item, dirs_exist_ok=True)
                for copied in src_item.rglob("*"):
                    if copied.is_file():
                        copied_files.add(str(copied.relative_to(src)))
            elif src_item.is_file():
                dst_item.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_item, dst_item)
                copied_files.add(str(rel))

    settings_dir.mkdir(exist_ok=True)
    settings = {
        "permissions": {
            "allow": ["Bash(*)", "Read(*)", "Write(*)", "Edit(*)"],
            "deny": [],
        }
    }
    (settings_dir / "settings.json").write_text(json.dumps(settings, indent=2))
    if not already_initialized:
        (settings_dir / "source_files.json").write_text(json.dumps(sorted(copied_files), indent=2))
        spec_file.write_text(json.dumps(list(source_files)))

    return str(ws)


def _manifest_paths(ws: Path) -> set[Path]:
    manifest = ws / ".claude" / "source_files.json"
    if not manifest.exists():
        return set()
    try:
        return {Path(p) for p in json.loads(manifest.read_text())}
    except (json.JSONDecodeError, TypeError):
        return set()


def diff(job_id: str, cwd: str) -> str:
    ws = workspace_path(job_id)
    src = Path(cwd)
    lines = []
    paths = set()

    paths.update(_manifest_paths(ws))

    if ws.exists():
        for ws_file in ws.rglob("*"):
            if ws_file.is_file():
                rel = ws_file.relative_to(ws)
                if not str(rel).startswith(".claude"):
                    paths.add(rel)

    for rel in sorted(paths):
        ws_file = ws / rel
        orig = src / rel
        ws_text = ws_file.read_text(errors="replace").splitlines(keepends=True) if ws_file.exists() else []
        orig_text = orig.read_text(errors="replace").splitlines(keepends=True) if orig.exists() else []
        chunk = list(difflib.unified_diff(orig_text, ws_text, fromfile=str(orig), tofile=str(ws_file)))
        lines.extend(chunk)

    return "".join(lines)


def apply(job_id: str, cwd: str) -> None:
    # KNOWN LIMITATION: apply() only copies files that exist in the workspace.
    # If the night session deleted a source file, the original is NOT removed.
    # Review the diff carefully and delete any such files manually.
    ws = workspace_path(job_id)
    src = Path(cwd).resolve()
    ws_root = ws.resolve()

    if not ws.exists() or not ws.is_dir():
        raise SandboxError(f"workspace does not exist: {job_id}")
    if not src.exists() or not src.is_dir():
        raise SandboxError(f"cwd does not exist or is not a directory: {cwd}")

    for ws_file in sorted(ws.rglob("*")):
        if not ws_file.is_file():
            continue
        rel = ws_file.relative_to(ws)
        if str(rel).startswith(".claude"):
            continue
        real_ws_file = ws_file.resolve()
        dest = (src / rel).resolve()
        if not _is_relative_to(real_ws_file, ws_root):
            raise SandboxError(f"workspace file escapes sandbox: {rel}")
        if not _is_relative_to(dest, src):
            raise SandboxError(f"destination escapes cwd: {rel}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ws_file, dest)


def discard(job_id: str) -> None:
    ws = workspace_path(job_id)
    if ws.exists():
        shutil.rmtree(ws)
