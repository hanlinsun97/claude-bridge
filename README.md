# claude-autoresumer

**Pin a Claude Code session and auto-resume it across usage-limit resets.**

You're deep into a Claude Code task. Your 5-hour Pro window is about to expire. Today, that means: lose the conversation, manually restart in 5 hours, hope the model figures out where you left off from file state alone.

This tool fixes that. Queue the task, arm the daemon, walk away. When the limit hits, the daemon parses the exact reset time from Claude's error, sleeps until that moment, then resumes the **same Claude Code session** — full reasoning, decisions, and partial-work context all preserved.

> macOS only. The daemon is a LaunchAgent; closing Terminal is fine, but the laptop must stay awake (`caffeinate -dimsu &` or System Settings → Battery → "Prevent automatic sleeping when display is off").

## Why this exists

Built-in `--auto-resume` is the [#1 most-requested Claude Code feature](https://github.com/anthropics/claude-code/issues/36320) and has been open across at least six issues ([#36320](https://github.com/anthropics/claude-code/issues/36320), [#18980](https://github.com/anthropics/claude-code/issues/18980), [#26775](https://github.com/anthropics/claude-code/issues/26775), [#35744](https://github.com/anthropics/claude-code/issues/35744), [#38263](https://github.com/anthropics/claude-code/issues/38263), [#47276](https://github.com/anthropics/claude-code/issues/47276)) without resolution. This is the stopgap until Anthropic ships it.

## How it differs from existing tools

| Capability | [`terryso/claude-auto-resume`](https://github.com/terryso/claude-auto-resume) | [`sleepless-agent`](https://github.com/context-machine-lab/sleepless-agent) | **claude-autoresumer** |
|---|---|---|---|
| Survives terminal close | ❌ foreground sleep loop | ✅ daemon | ✅ LaunchAgent |
| Survives reboot (requires re-login) | ❌ lost | ✅ via persistent queue | ✅ via persistent queue + RunAtLoad |
| Pinned session ID (precise resume) | ❌ `-c` (last conv in cwd, fragile) | ❌ runs next task fresh | ✅ `--session-id <uuid>` + `--resume <uuid>` |
| Sandboxed execution | ❌ runs in cwd | ✅ per-task workspace | ✅ per-task workspace + manifest guard |
| Knows exact reset time | ✅ parses timestamp | ❌ polls usage % | ✅ parses timestamp (falls back to polling) |
| Scope | one-shot script | full 24/7 AgentOS with Slack | focused: arm-and-forget single sessions |
| Cross-platform | ✅ macOS/Linux/Windows | ✅ macOS/Linux | ❌ macOS only |
| Setup | drop-in script | pip + Slack app + SQLite | pip + plist |

The tradeoff: claude-autoresumer is **macOS-only** and a heavier install than a shell script. In return: it survives reboots and laptop-lid-close, pins sessions precisely (no `-c` collisions), sandboxes every run, and skips probe calls when the reset moment is known.

## Install

```bash
git clone https://github.com/hanlinsun97/claude-autoresumer.git
cd claude-autoresumer
pip install -e .
claude-autoresumer install-skill   # makes the Claude Code skill available
```

Requires: Python 3.11+, the `claude` CLI on PATH, macOS.

## Quickstart

### Path A — from inside a Claude Code session (recommended)

If you're already mid-conversation with Claude and notice the usage indicator getting low, just say:

> "I'm running low on usage. Set up auto-resume for this task."

Claude invokes the `claude-autoresumer` skill and walks you through: a continuation prompt, which files to include, retry window. Then it runs `queue add` and `start` for you.

### Path B — from the terminal directly

```bash
claude-autoresumer queue add \
  --prompt "Continue refactoring src/auth/session.py to use TokenStore. \
            I've migrated login() and logout(); refresh() still uses the old \
            direct-DB path. Convert refresh() to TokenStore.get()/set() with \
            the same semantics, then update tests/test_session.py." \
  --model claude-opus-4-7 \
  --cwd /Users/me/project \
  --file src/auth/ \
  --file tests/test_session.py \
  --max-retry-hours 24

claude-autoresumer start
claude-autoresumer status
```

When you're back at the keyboard:

```bash
claude-autoresumer status                              # see done / pending / failed
claude-autoresumer workspaces diff <job_id>            # review changes
claude-autoresumer workspaces apply <job_id>           # accept into your repo
claude-autoresumer workspaces discard <job_id>         # or throw it away
```

## How session resumption works

The daemon ticks every 10 minutes via launchd. On each tick:

1. **Check the queue.** If empty, the daemon self-uninstalls — it's arm-and-forget, not a persistent service.
2. **Skip if waiting.** If the current job has a known `next_eligible_at` and that moment hasn't arrived, return immediately without burning a probe call.
3. **Probe.** Run `claude -p .` to check if usage is available.
4. **First run of the job:** generate a UUID, run `claude --session-id <uuid> --model <m> -p <full_prompt>`, persist the UUID.
5. **Retry after a defer:** run `claude --resume <stored_uuid> -p "Usage limit was hit and has now reset. The workspace reflects your prior progress. Continue the task from where you stopped."` — the original prompt is **not** re-sent; the model already has it in the resumed conversation.
6. **On usage-limit error:** parse the reset time from the error output (three formats supported), persist it as `next_eligible_at`, mark job pending. The next tick will skip the probe until that time.
7. **On unrecoverable error or retry-window-expired:** mark failed, log, notify.

## Sandbox model

Every job runs in its own isolated workspace at `~/.claude-autoresumer/workspaces/<job_id>/`:

- Only the files you list via `--file` are copied in.
- Path traversal is rejected at queue time (`../`, absolute paths) and again in the sandbox layer.
- A `.claude/settings.json` grants broad permissions **only inside the sandbox**.
- A `.claude/source_spec.json` records the input file list; if a deferred retry sees a changed spec or missing manifest, it refuses to proceed rather than silently clobber in-progress edits.
- `workspaces apply` copies files back to your repo. **Deletions are not propagated** — use `workspaces diff` to spot them, then delete originals manually.

## Commands

```
claude-autoresumer queue add          Queue a new job
claude-autoresumer queue list         List all jobs
claude-autoresumer queue remove ID    Remove a pending job
claude-autoresumer queue clear        Remove all pending jobs
claude-autoresumer start              Arm the LaunchAgent daemon
claude-autoresumer stop               Disarm
claude-autoresumer status             Show daemon + queue summary
claude-autoresumer workspaces list    List workspaces
claude-autoresumer workspaces diff ID Show diff vs originals
claude-autoresumer workspaces apply ID Apply changes to originals
claude-autoresumer workspaces discard ID  Delete a workspace
claude-autoresumer probe              Check if usage is available now
claude-autoresumer install-skill      Install the Claude Code skill
```

## Caveats to know

- **macOS LaunchAgents pause on sleep.** Plug in + disable sleep, or use `caffeinate`.
- **Only completed Claude turns are persisted.** If the limit fires inside an in-flight assistant turn, that turn's reasoning is lost — resume picks up from the last completed turn plus the workspace file state.
- **`apply` does not propagate deletions.** Review `workspaces diff` before applying.
- **One job at a time.** The queue runs jobs sequentially; the headline use case is pinning one session, not parallel orchestration.
- **Pro plan only.** This rides your Claude Code subscription via the `claude` CLI; it doesn't burn API credits.

## Status

Pre-1.0. Surface area is intentionally narrow. The two values it earns: cross-reset session continuity, and a sandbox that prevents `--dangerously-skip-permissions` from touching your real files. Bugs and issues welcome.

## License

MIT.
