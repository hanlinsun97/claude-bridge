# claude-bridge

Queue Claude Code jobs to run overnight, unattended. Go to bed; wake up to finished work.

## How it works

1. Before bed, queue one or more jobs (or let Claude do it via the built-in skill)
2. Arm the daemon: `claude-bridge start`
3. The daemon wakes every 10 minutes and checks if your Claude usage has reset
4. When usage is available, it runs the next job headlessly in an isolated sandbox
5. In the morning: `claude-bridge status`, review diffs, apply the changes you want

Your original files are **never touched**. All work happens in `~/.claude-bridge/workspaces/`.

## Requirements

- macOS (uses LaunchAgents for scheduling)
- Python 3.11+
- [Claude Code](https://claude.ai/code) CLI installed and authenticated

## Install

```bash
pip install claude-bridge
claude-bridge install-skill   # adds the Claude Code skill to ~/.claude/skills/
```

## Quick start

```bash
# Queue a job (self-heal policy is set per job, not on the daemon)
claude-bridge queue add \
  --prompt "Refactor the auth module for clarity and add missing tests" \
  --model claude-opus-4-7 \
  --file src/auth/ \
  --file tests/test_auth.py \
  --workflow tdd \
  --self-heal 8h

# Arm the daemon
claude-bridge start

# Morning: check results
claude-bridge status
claude-bridge workspaces diff <job_id>
claude-bridge workspaces apply <job_id>
```

> **Note:** After the queue drains, the daemon stops and uninstalls itself. To run
> more jobs, re-queue them and re-arm with `claude-bridge start`.

## From inside Claude Code

When your session is running low, just say: **"set up overnight continuation"**

Claude will write the checkpoint, ask which files to include, and arm the daemon for you.

## Workflow templates

| Template | What it does |
|----------|-------------|
| `minimal` | Just execute the prompt |
| `tdd` | Write tests first, then implement, then code-review |
| `research` | Execute, iterate twice with Codex, then request review |
| `thorough` | Plan first, implement, iterate 3× with Codex, dual-approach validation, security review |

## Self-healing options

| Flag | Behaviour |
|------|-----------|
| `--self-heal always` | Retry across unlimited usage resets |
| `--self-heal 8h` | Stop retrying after 8 hours (default) |
| `--self-heal 3x` | Stop after consuming 3 usage resets |
| `--no-self-heal` | One shot only |

## Safety

- Original files are never modified — all work is in `~/.claude-bridge/workspaces/<job_id>/`
- You explicitly `apply` changes you want; everything else is discarded
- The workspace has a `.claude/settings.json` granting the night session broad permissions **only inside the sandbox**
- Queued source paths must be relative to `--cwd`; absolute paths and `..` segments are rejected

## Session continuity across usage resets

When a job hits the usage limit mid-run, claude-bridge defers it back to `pending`
and retries on the next tick after the quota resets. Each job is pinned to a
single Claude Code session ID (via `--session-id` on first run, `--resume` on
retry), so the model keeps its prior reasoning, decisions, and partial-work
context across the reset — not just the files on disk. The retry sends a short
continuation prompt (`"Usage limit was hit and has now reset. The workspace
reflects your prior progress. Continue the task from where you stopped."`)
instead of re-sending the original prompt verbatim.

Caveat: only **completed** assistant turns are persisted by Claude Code. If the
limit fires inside an in-flight turn, that turn's reasoning is lost; resume picks
up from the last completed turn plus the workspace file state.

## Commands

```
claude-bridge queue add          Queue a new job
claude-bridge queue list         List all jobs
claude-bridge queue remove       Remove a pending job
claude-bridge queue clear        Remove all pending jobs
claude-bridge start              Arm the daemon
claude-bridge stop               Disarm the daemon
claude-bridge status             Show daemon + queue summary
claude-bridge workspaces list    List workspaces
claude-bridge workspaces diff    Show diff vs originals
claude-bridge workspaces apply   Apply changes (see note below)
claude-bridge workspaces discard Delete a workspace
claude-bridge probe              Check if usage is available now
claude-bridge install-skill      Install the Claude Code skill
```

## Known limitations

### `workspaces apply` does not propagate deletions

`apply` copies files from the workspace back to your project. If the night session
deleted a source file, the original is **not** removed. `workspaces diff` shows
the deletion so you can delete the original manually before or after applying.

## License

MIT
