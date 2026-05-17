import subprocess
import plistlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from claude_bridge.probe import probe, ProbeError
from claude_bridge import queue as q_mod
from claude_bridge import sandbox
from claude_bridge.workflow import compile_prompt
from claude_bridge.notify import notify

LAUNCH_AGENTS_DIR = str(Path.home() / "Library" / "LaunchAgents")
PLIST_LABEL = "com.claude-bridge"
PLIST_NAME = f"{PLIST_LABEL}.plist"


def generate_plist(bridge_home: str) -> str:
    logs_dir = Path(bridge_home) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "Label": PLIST_LABEL,
        "ProgramArguments": ["claude-bridge", "_tick", "--home", bridge_home],
        "StartInterval": 600,
        "RunAtLoad": True,
        "StandardOutPath": str(logs_dir / "daemon.log"),
        "StandardErrorPath": str(logs_dir / "daemon.err"),
    }
    return plistlib.dumps(data).decode()


def _plist_path() -> Path:
    return Path(LAUNCH_AGENTS_DIR) / PLIST_NAME


def install(bridge_home: str) -> None:
    plist_content = generate_plist(bridge_home=bridge_home)
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist_content)
    subprocess.run(["launchctl", "load", "-w", str(path)], check=True)


def uninstall() -> None:
    path = _plist_path()
    if path.exists():
        subprocess.run(["launchctl", "unload", str(path)], check=False)
        path.unlink()


def _run_job(job, bridge_home: str) -> bool:
    ws = sandbox.create(job_id=job.id, cwd=job.cwd, source_files=job.source_files)
    q_mod.update(job.id, status="running", started_at=datetime.now(timezone.utc).isoformat(), workspace=ws)

    prompt = compile_prompt(base_prompt=job.prompt, workflow=job.workflow, workspace_path=ws)

    prompt_file = Path(bridge_home) / "logs" / f"{job.id}-prompt.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt)

    output_log = Path(bridge_home) / "logs" / f"{job.id}-output.txt"
    output_log.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["claude", "--dangerously-skip-permissions", "-p", prompt],
            cwd=ws,
            capture_output=True,
            text=True,
            timeout=14400,
        )
        success = result.returncode == 0
        with open(output_log, "w") as f:
            f.write(result.stdout or "")
            if result.stderr:
                f.write("\n--- STDERR ---\n")
                f.write(result.stderr)
    except subprocess.TimeoutExpired:
        success = False
        q_mod.update(job.id, error="Job timed out after 4 hours")
        output_log.write_text("Job timed out after 4 hours")

    status = "done" if success else "failed"
    q_mod.update(job.id, status=status, finished_at=datetime.now(timezone.utc).isoformat())
    return success


def _record_start(bridge_home: str) -> None:
    path = Path(bridge_home) / "daemon_started_at.txt"
    if not path.exists():
        path.write_text(datetime.now(timezone.utc).isoformat())


def _increment_reset_count(bridge_home: str) -> int:
    path = Path(bridge_home) / "reset_count.txt"
    count = int(path.read_text()) + 1 if path.exists() else 1
    path.write_text(str(count))
    return count


def _policy_expired(job, bridge_home: str) -> bool:
    sh = job.self_healing
    if sh.mode == "always":
        return False
    if sh.mode == "single_session":
        return False

    if sh.max_hours is not None:
        start_file = Path(bridge_home) / "daemon_started_at.txt"
        if start_file.exists():
            started = datetime.fromisoformat(start_file.read_text())
            elapsed = (datetime.now(timezone.utc) - started).total_seconds() / 3600
            if elapsed >= sh.max_hours:
                return True

    if sh.max_resets is not None:
        reset_file = Path(bridge_home) / "reset_count.txt"
        count = int(reset_file.read_text()) if reset_file.exists() else 0
        if count >= sh.max_resets:
            return True

    return False


def tick(bridge_home: Optional[str] = None) -> str:
    from claude_bridge.queue import _home
    if bridge_home is None:
        bridge_home = str(_home())

    _record_start(bridge_home)

    try:
        available = probe()
    except ProbeError:
        return "probe_error"

    if not available:
        return "no_usage"

    job = q_mod.next_pending()
    if job is None:
        uninstall()
        return "queue_empty"

    if _policy_expired(job, bridge_home):
        notify("claude-bridge: self-healing policy expired, stopping.")
        uninstall()
        return "policy_expired"

    _increment_reset_count(bridge_home)
    success = _run_job(job, bridge_home=bridge_home)
    label = job.prompt[:60]
    notify(f"claude-bridge: job done — {label}" if success else f"claude-bridge: job FAILED — {label}")
    return "ran_job"
