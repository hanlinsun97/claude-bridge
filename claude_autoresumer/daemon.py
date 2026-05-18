import json
import subprocess
import plistlib
import sys
import traceback
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from claude_autoresumer.probe import probe, ProbeError, USAGE_LIMIT_PATTERNS, parse_reset_at
from claude_autoresumer import queue as q_mod
from claude_autoresumer import sandbox
from claude_autoresumer.notify import notify

RESUME_PROMPT = (
    "Usage limit was hit and has now reset. The workspace reflects your "
    "prior progress. Continue the task from where you stopped."
)

LAUNCH_AGENTS_DIR = str(Path.home() / "Library" / "LaunchAgents")
PLIST_LABEL = "com.claude-autoresumer"
PLIST_NAME = f"{PLIST_LABEL}.plist"

# Upper bound for accepting a parsed reset timestamp. Anthropic's longest
# documented window today is the 7-day weekly limit; 8 days gives a buffer.
RESET_HORIZON_DAYS = 8


def generate_plist(bridge_home: str) -> str:
    logs_dir = Path(bridge_home) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "Label": PLIST_LABEL,
        "ProgramArguments": [sys.executable, "-m", "claude_autoresumer.cli", "_tick", "--home", bridge_home],
        "StartInterval": 600,
        "RunAtLoad": True,
        "StandardOutPath": str(logs_dir / "daemon.log"),
        "StandardErrorPath": str(logs_dir / "daemon.err"),
    }
    return plistlib.dumps(data).decode()


def _plist_path() -> Path:
    return Path(LAUNCH_AGENTS_DIR) / PLIST_NAME


def _state_path(bridge_home: str) -> Path:
    return Path(bridge_home) / "state.json"


def read_state(bridge_home: str) -> dict:
    """Return the persisted heartbeat state, or {} if absent/corrupt."""
    path = _state_path(bridge_home)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(bridge_home: str, **updates) -> None:
    path = _state_path(bridge_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = read_state(bridge_home)
    state.update(updates)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def install(bridge_home: str) -> None:
    plist_content = generate_plist(bridge_home=bridge_home)
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist_content)
    subprocess.run(["launchctl", "load", "-w", str(path)], check=True)
    _write_state(
        bridge_home,
        armed_at=datetime.now(timezone.utc).isoformat(),
        last_tick_at=None,
        last_tick_result=None,
        tick_count=0,
    )


def uninstall() -> None:
    path = _plist_path()
    if path.exists():
        subprocess.run(["launchctl", "unload", str(path)], check=False)
        path.unlink()


def _usage_limit_hit(output: str) -> bool:
    return any(re.search(pattern, output, re.IGNORECASE) for pattern in USAGE_LIMIT_PATTERNS)


def _retry_window_expired(job) -> bool:
    """True if the job has been retrying for longer than max_retry_hours."""
    if not job.started_at:
        return False
    started = datetime.fromisoformat(job.started_at)
    elapsed_hours = (datetime.now(timezone.utc) - started).total_seconds() / 3600
    return elapsed_hours >= job.max_retry_hours


def _run_job(job, bridge_home: str) -> str:
    prompt_file = Path(bridge_home) / "logs" / f"{job.id}-prompt.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    output_log = Path(bridge_home) / "logs" / f"{job.id}-output.txt"
    output_log.parent.mkdir(parents=True, exist_ok=True)

    success = False
    try:
        ws = sandbox.create(job_id=job.id, cwd=job.cwd, source_files=job.source_files)

        if job.session_id is None:
            session_id = str(uuid.uuid4())
            prompt = job.prompt
            cmd = [
                "claude", "--dangerously-skip-permissions",
                "--model", job.model,
                "--session-id", session_id,
                "-p", prompt,
            ]
            q_mod.update(
                job.id,
                status="running",
                started_at=job.started_at or datetime.now(timezone.utc).isoformat(),
                workspace=ws,
                session_id=session_id,
                next_eligible_at=None,
            )
        else:
            prompt = RESUME_PROMPT
            cmd = [
                "claude", "--dangerously-skip-permissions",
                "--resume", job.session_id,
                "-p", prompt,
            ]
            q_mod.update(
                job.id,
                status="running",
                workspace=ws,
                next_eligible_at=None,
            )

        prompt_file.write_text(prompt)

        result = subprocess.run(
            cmd,
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
        if not success:
            combined = f"{result.stdout}\n{result.stderr}"
            error = (result.stderr or result.stdout or f"claude exited with status {result.returncode}").strip()
            if _usage_limit_hit(combined) and not _retry_window_expired(job):
                reset_at = parse_reset_at(combined)
                # Sanity-cap an absurd reset time. Anthropic's longest known
                # window is the 7-day weekly limit; we allow 8 days for buffer.
                # Anything further out is treated as a malformed timestamp,
                # dropped, and logged so the operator can see why polling
                # fell back instead of a known wait.
                if reset_at is not None:
                    horizon = datetime.now(timezone.utc) + timedelta(days=RESET_HORIZON_DAYS)
                    if reset_at > horizon:
                        print(
                            f"[claude-autoresumer] dropping reset timestamp "
                            f"{reset_at.isoformat()} — more than {RESET_HORIZON_DAYS}d "
                            f"out, falling back to polling.",
                            file=sys.stderr,
                        )
                        reset_at = None
                q_mod.update(
                    job.id,
                    status="pending",
                    error=error[:2000],
                    next_eligible_at=reset_at.isoformat() if reset_at else None,
                )
                return "deferred"
            q_mod.update(job.id, error=error[:2000])
    except subprocess.TimeoutExpired as e:
        partial = ""
        if e.stdout:
            partial += e.stdout if isinstance(e.stdout, str) else e.stdout.decode("utf-8", errors="replace")
        if e.stderr:
            partial += "\n" + (e.stderr if isinstance(e.stderr, str) else e.stderr.decode("utf-8", errors="replace"))
        error_msg = "Job timed out after 4 hours"
        output_log.write_text(f"{error_msg}\n\nPartial output:\n{partial}")
        success = False
        q_mod.update(job.id, error=error_msg)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        output_log.write_text(f"{error_msg}\n\n{traceback.format_exc()}")
        success = False
        q_mod.update(job.id, error=error_msg)

    status = "done" if success else "failed"
    q_mod.update(job.id, status=status, finished_at=datetime.now(timezone.utc).isoformat())
    return status


def tick(bridge_home: Optional[str] = None) -> str:
    from claude_autoresumer.queue import _home
    if bridge_home is None:
        bridge_home = str(_home())

    result = "error"
    try:
        result = _tick_inner(bridge_home)
        return result
    finally:
        state = read_state(bridge_home)
        _write_state(
            bridge_home,
            last_tick_at=datetime.now(timezone.utc).isoformat(),
            last_tick_result=result,
            tick_count=state.get("tick_count", 0) + 1,
        )


def _tick_inner(bridge_home: str) -> str:
    job = q_mod.next_pending()
    if job is None:
        # When the queue drains the daemon uninstalls itself. The daemon is a
        # one-shot arm, not a persistent service.
        uninstall()
        return "queue_empty"

    # Check retry-window BEFORE wait-for-reset: a bogus far-future reset
    # timestamp from Claude must not park the job indefinitely past its
    # retry budget.
    if _retry_window_expired(job):
        notify(f"claude-autoresumer: retry window expired — {job.prompt[:60]}")
        q_mod.update(job.id, status="failed", finished_at=datetime.now(timezone.utc).isoformat(),
                     error=f"max_retry_hours ({job.max_retry_hours}h) exceeded")
        return "retry_window_expired"

    # If the job has a known reset time and it hasn't arrived, skip the probe
    # to save a Claude call.
    if job.next_eligible_at:
        try:
            eligible = datetime.fromisoformat(job.next_eligible_at)
            if datetime.now(timezone.utc) < eligible:
                return "waiting_for_reset"
        except ValueError:
            pass

    try:
        available = probe()
    except ProbeError:
        return "probe_error"

    if not available:
        return "no_usage"

    outcome = _run_job(job, bridge_home=bridge_home)
    label = job.prompt[:60]
    if outcome == "deferred":
        notify(f"claude-autoresumer: usage limit hit, will resume on reset — {label}")
        return "deferred_usage_limit"
    notify(f"claude-autoresumer: job done — {label}" if outcome == "done" else f"claude-autoresumer: job FAILED — {label}")
    return "ran_job"
