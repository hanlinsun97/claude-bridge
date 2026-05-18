import json
import sys
from pathlib import Path
import click
from claude_autoresumer.models import Job
from claude_autoresumer import queue as q_mod
from claude_autoresumer import sandbox, daemon
from claude_autoresumer.probe import probe as _probe_fn, ProbeError


@click.group()
def cli():
    """Pin a Claude Code session and auto-resume it across usage-limit resets."""


# ── Queue ─────────────────────────────────────────────────────────────────────

@cli.group()
def queue():
    """Manage the job queue."""


@queue.command("add")
@click.option("--prompt", required=True, help="Task prompt for Claude")
@click.option("--model", default="claude-sonnet-4-6", show_default=True)
@click.option("--cwd", default=None, help="Working directory (default: current dir)")
@click.option("--file", "file_items", multiple=True, help="File/dir to include in sandbox; may be repeated")
@click.option("--max-retry-hours", default=24.0, show_default=True, type=float,
              help="Give up if the job hasn't completed within this many hours of its first start")
def queue_add(prompt, model, cwd, file_items, max_retry_hours):
    """Add a job to the queue."""
    source_files = list(file_items)
    effective_cwd = cwd or str(Path.cwd())
    _validate_job_inputs(effective_cwd, source_files)
    job = Job(
        type="task",
        prompt=prompt,
        model=model,
        cwd=effective_cwd,
        source_files=source_files,
        max_retry_hours=max_retry_hours,
    )
    q_mod.add(job)
    click.echo(f"Queued job {job.id[:8]}: {job.prompt[:60]}")

    # If the daemon is armed, fire a tick inline so the user sees immediate
    # confirmation instead of waiting up to 10 min for the next launchd tick.
    plist = Path.home() / "Library" / "LaunchAgents" / "com.claude-autoresumer.plist"
    if plist.exists():
        click.echo("Daemon armed — running first tick...")
        try:
            result = daemon.tick()
            click.echo(f"  tick result: {result}")
        except Exception as e:
            click.echo(f"  tick error: {type(e).__name__}: {e}", err=True)
    else:
        click.echo("Daemon NOT armed. Run `claude-autoresumer start` to arm.")


@queue.command("list")
def queue_list():
    """List all jobs."""
    jobs = q_mod.load().jobs
    if not jobs:
        click.echo("Queue is empty.")
        return
    for job in jobs:
        click.echo(f"[{job.status:8}] {job.id[:8]} | {job.prompt[:60]}")


@queue.command("remove")
@click.argument("job_id")
def queue_remove(job_id):
    """Remove a pending job."""
    q_mod.remove(job_id)
    click.echo(f"Removed {job_id}")


@queue.command("clear")
def queue_clear():
    """Remove all pending jobs."""
    q_mod.clear_pending()
    click.echo("Cleared all pending jobs.")


# ── Daemon ────────────────────────────────────────────────────────────────────

@cli.command()
def start():
    """Install and arm the LaunchAgent daemon."""
    from claude_autoresumer.queue import _home
    home = str(_home())
    daemon.install(bridge_home=home)
    click.echo("Daemon armed.")
    # RunAtLoad fires a tick async via launchd, but run one inline too so the
    # user gets an immediate result rather than waiting on launchd.
    try:
        result = daemon.tick(bridge_home=home)
        click.echo(f"First tick: {result}")
    except Exception as e:
        click.echo(f"First tick error: {type(e).__name__}: {e}", err=True)
    click.echo("Use 'claude-autoresumer status' to monitor progress.")


@cli.command()
def stop():
    """Unload and remove the LaunchAgent."""
    daemon.uninstall()
    click.echo("Daemon stopped.")


@cli.command()
def status():
    """Show daemon state and queue summary."""
    jobs = q_mod.load().jobs
    counts = {"pending": 0, "running": 0, "done": 0, "failed": 0}
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1

    plist = Path.home() / "Library" / "LaunchAgents" / "com.claude-autoresumer.plist"
    daemon_state = "armed" if plist.exists() else "stopped"

    click.echo(f"Daemon:  {daemon_state}")

    from claude_autoresumer.queue import _home
    state = daemon.read_state(str(_home()))
    if state.get("armed_at"):
        click.echo(f"Armed:   {state['armed_at']}")
    if state.get("last_tick_at"):
        click.echo(
            f"Last tick: {state['last_tick_at']} → "
            f"{state.get('last_tick_result') or '?'}  (#{state.get('tick_count', 0)})"
        )
    elif daemon_state == "armed":
        click.echo("Last tick: (none yet — first tick should fire within 10 min)")

    click.echo(f"Queue:   {counts['pending']} pending, {counts['running']} running, "
               f"{counts['done']} done, {counts['failed']} failed")
    for job in jobs:
        marker = {"pending": "·", "running": "▶", "done": "✓", "failed": "✗"}.get(job.status, "?")
        eta = ""
        if job.next_eligible_at and job.status == "pending":
            eta = f"  (eligible: {job.next_eligible_at})"
        click.echo(f"  {marker} {job.id[:8]} {job.prompt[:50]}{eta}")


@cli.command("_tick", hidden=True)
@click.option("--home", "bridge_home", default=None)
def _tick(bridge_home):
    result = daemon.tick(bridge_home=bridge_home)
    click.echo(f"tick: {result}")


# ── Workspaces ────────────────────────────────────────────────────────────────

@cli.group()
def workspaces():
    """Inspect and manage sandboxed workspaces."""


@workspaces.command("list")
def workspaces_list():
    """List all workspaces."""
    from claude_autoresumer.queue import _home
    ws_root = _home() / "workspaces"
    if not ws_root.exists() or not any(ws_root.iterdir()):
        click.echo("No workspaces found.")
        return
    for ws in sorted(ws_root.iterdir()):
        size = sum(f.stat().st_size for f in ws.rglob("*") if f.is_file())
        click.echo(f"  {ws.name}  ({size // 1024} KB)")


@workspaces.command("diff")
@click.argument("job_id")
@click.option("--cwd", default=None)
def workspaces_diff(job_id, cwd):
    """Show diff between workspace and original files."""
    job = next((j for j in q_mod.load().jobs if j.id.startswith(job_id)), None)
    effective_cwd = cwd or (job.cwd if job else None)
    if not effective_cwd:
        click.echo("--cwd required when job not in queue", err=True)
        sys.exit(1)
    full_id = job.id if job else job_id
    click.echo(sandbox.diff(job_id=full_id, cwd=effective_cwd))


@workspaces.command("apply")
@click.argument("job_id")
@click.option("--cwd", default=None)
def workspaces_apply(job_id, cwd):
    """Copy workspace changes back to the original location."""
    job = next((j for j in q_mod.load().jobs if j.id.startswith(job_id)), None)
    effective_cwd = cwd or (job.cwd if job else None)
    if not effective_cwd:
        click.echo("--cwd required when job not in queue", err=True)
        sys.exit(1)
    full_id = job.id if job else job_id
    sandbox.apply(job_id=full_id, cwd=effective_cwd)
    click.echo(f"Applied changes from workspace {job_id[:8]} to {effective_cwd}")


@workspaces.command("discard")
@click.argument("job_id")
def workspaces_discard(job_id):
    """Delete a workspace."""
    job = next((j for j in q_mod.load().jobs if j.id.startswith(job_id)), None)
    full_id = job.id if job else job_id
    sandbox.discard(job_id=full_id)
    click.echo(f"Discarded workspace {job_id[:8]}")


# ── Utility ───────────────────────────────────────────────────────────────────

@cli.command("probe")
def probe_cmd():
    """Test if Claude Code usage is currently available."""
    try:
        available = _probe_fn()
    except ProbeError as e:
        click.echo(f"Probe error: {e}", err=True)
        click.echo("Run `claude -p .` directly to see the full error.", err=True)
        sys.exit(2)
    if available:
        click.echo("Usage available.")
        sys.exit(0)
    click.echo("Usage limit active — not available yet.")
    sys.exit(1)


@cli.command("install-skill")
def install_skill():
    """Copy the claude-autoresumer skill to ~/.claude/skills/."""
    import shutil
    src = Path(__file__).parent / "skills" / "claude-autoresumer.md"
    dest_dir = Path.home() / ".claude" / "skills"
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_dir / "claude-autoresumer.md")
    click.echo(f"Skill installed to {dest_dir / 'claude-autoresumer.md'}")


def _validate_job_inputs(cwd: str, source_files: list[str]) -> None:
    root = Path(cwd).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise click.BadParameter(f"cwd does not exist or is not a directory: {cwd}", param_hint="--cwd")

    for item in source_files:
        clean = item.rstrip("/")
        if not clean:
            raise click.BadParameter("source file path cannot be empty", param_hint="--file")
        rel = Path(clean)
        if rel.is_absolute() or ".." in rel.parts:
            raise click.BadParameter(f"source file path must stay inside cwd: {item}", param_hint="--file")
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as e:
            raise click.BadParameter(f"source file path escapes cwd: {item}", param_hint="--file") from e
        if not candidate.exists():
            raise click.BadParameter(f"source file does not exist: {item}", param_hint="--file")


if __name__ == "__main__":
    cli()
