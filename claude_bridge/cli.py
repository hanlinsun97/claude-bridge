import json
import sys
from pathlib import Path
import click
from claude_bridge.models import Job, SelfHealingConfig
from claude_bridge import queue as q_mod
from claude_bridge import sandbox, daemon
from claude_bridge.workflow import apply_template, WORKFLOW_TEMPLATES
from claude_bridge.probe import probe as _probe_fn, ProbeError


@click.group()
def cli():
    """Queue and run Claude Code jobs overnight, unattended."""


# ── Queue ─────────────────────────────────────────────────────────────────────

@cli.group()
def queue():
    """Manage the overnight job queue."""


@queue.command("add")
@click.option("--prompt", default=None, help="Task prompt for Claude")
@click.option("--model", default="claude-sonnet-4-6", show_default=True)
@click.option("--cwd", default=None, help="Working directory (default: current dir)")
@click.option("--files", default="", help="Space-separated files/dirs to include in sandbox")
@click.option("--file", "file_items", multiple=True, help="File/dir to include in sandbox; may be repeated")
@click.option("--workflow", "workflow_template", default="minimal",
              type=click.Choice(list(WORKFLOW_TEMPLATES)), show_default=True)
@click.option("--self-heal", "self_heal", default="8h")
@click.option("--no-self-heal", "self_heal", flag_value="none")
@click.option("--resume", is_flag=True, default=False)
@click.option("--checkpoint", default=None, type=click.Path(exists=True))
def queue_add(prompt, model, cwd, files, file_items, workflow_template, self_heal, resume, checkpoint):
    """Add a job to the queue."""
    if resume:
        if not checkpoint:
            click.echo("--checkpoint is required with --resume", err=True)
            sys.exit(1)
        try:
            data = json.loads(Path(checkpoint).read_text())
        except (json.JSONDecodeError, OSError) as e:
            raise click.ClickException(f"Could not read checkpoint: {e}") from e
        source_files = data.get("source_files", [])
        _validate_job_inputs(data.get("cwd", cwd or str(Path.cwd())), source_files)
        job = Job(
            type="resume",
            prompt=data["prompt"],
            cwd=data.get("cwd", cwd or str(Path.cwd())),
            model=data.get("model", model),
            source_files=source_files,
            workflow=apply_template(workflow_template),
            self_healing=_parse_self_heal(self_heal),
        )
    else:
        if not prompt:
            click.echo("--prompt is required unless using --resume", err=True)
            sys.exit(1)
        source_files = [f for f in files.split() if f] + list(file_items)
        effective_cwd = cwd or str(Path.cwd())
        _validate_job_inputs(effective_cwd, source_files)
        job = Job(
            type="task",
            prompt=prompt,
            model=model,
            cwd=effective_cwd,
            source_files=source_files,
            workflow=apply_template(workflow_template),
            self_healing=_parse_self_heal(self_heal),
        )
    q_mod.add(job)
    click.echo(f"Queued job {job.id[:8]} ({job.type}): {job.prompt[:60]}")


@queue.command("list")
def queue_list():
    """List all jobs."""
    jobs = q_mod.load().jobs
    if not jobs:
        click.echo("Queue is empty.")
        return
    for job in jobs:
        click.echo(f"[{job.status:8}] {job.id[:8]} | {job.type:6} | {job.prompt[:60]}")


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
    """Install and arm the LaunchAgent daemon.

    Self-heal policy is set per-job via 'queue add --self-heal'.
    """
    from claude_bridge.queue import _home
    daemon.install(bridge_home=str(_home()))
    click.echo("Daemon armed.")
    click.echo("Use 'claude-bridge status' to monitor progress.")


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

    plist = Path.home() / "Library" / "LaunchAgents" / "com.claude-bridge.plist"
    daemon_state = "armed" if plist.exists() else "stopped"

    click.echo(f"Daemon:  {daemon_state}")
    click.echo(f"Queue:   {counts['pending']} pending, {counts['running']} running, "
               f"{counts['done']} done, {counts['failed']} failed")
    for job in jobs:
        marker = {"pending": "·", "running": "▶", "done": "✓", "failed": "✗"}.get(job.status, "?")
        click.echo(f"  {marker} {job.id[:8]} {job.prompt[:50]}")


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
    from claude_bridge.queue import _home
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
        if available:
            click.echo("Usage available.")
            sys.exit(0)
        else:
            click.echo("Usage limit active — not available yet.")
            sys.exit(1)
    except ProbeError as e:
        click.echo(f"Probe error: {e}", err=True)
        sys.exit(2)


@cli.command("install-skill")
def install_skill():
    """Copy the claude-bridge skill to ~/.claude/skills/."""
    import shutil
    src = Path(__file__).parent / "skills" / "claude-bridge.md"
    dest_dir = Path.home() / ".claude" / "skills"
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_dir / "claude-bridge.md")
    click.echo(f"Skill installed to {dest_dir / 'claude-bridge.md'}")


# ── Self-heal parser ──────────────────────────────────────────────────────────

def _parse_self_heal(value: str) -> SelfHealingConfig:
    if value in ("none", "no-self-heal"):
        return SelfHealingConfig(mode="single_session", max_hours=None, max_resets=None)
    if value == "always":
        return SelfHealingConfig(mode="always", max_hours=None, max_resets=None)
    if value.endswith("h"):
        return SelfHealingConfig(mode="time_bounded", max_hours=float(value[:-1]), max_resets=None)
    if value.endswith("x"):
        return SelfHealingConfig(mode="time_bounded", max_hours=None, max_resets=int(value[:-1]))
    raise click.BadParameter(
        f"Unrecognized self-heal format: {value!r}. "
        "Use 'always', 'Xh' (hours), 'Nx' (resets), or 'none'."
    )


def _validate_job_inputs(cwd: str, source_files: list[str]) -> None:
    root = Path(cwd).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise click.BadParameter(f"cwd does not exist or is not a directory: {cwd}", param_hint="--cwd")

    for item in source_files:
        clean = item.rstrip("/")
        if not clean:
            raise click.BadParameter("source file path cannot be empty", param_hint="--files")
        rel = Path(clean)
        if rel.is_absolute() or ".." in rel.parts:
            raise click.BadParameter(f"source file path must stay inside cwd: {item}", param_hint="--files")
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as e:
            raise click.BadParameter(f"source file path escapes cwd: {item}", param_hint="--files") from e
        if not candidate.exists():
            raise click.BadParameter(f"source file does not exist: {item}", param_hint="--files")


if __name__ == "__main__":
    cli()
