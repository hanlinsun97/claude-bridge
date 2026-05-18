---
name: claude-autoresumer
description: >
  Use when the user is approaching a Claude Code usage limit and wants the
  current task (or a queued task) to automatically resume once the limit
  resets. Also use for: "set up auto-resume", "pin this session", "continue
  this when usage comes back", "I'm running low — keep working on this."
---

# claude-autoresumer Skill

You are helping the user pin a Claude Code task so it survives a usage-limit reset.
The resumed session keeps full conversation memory — reasoning, decisions, and
partial-work context all carry across the reset, not just the files on disk.

## Step 1: Confirm intent

Ask: "Should I pin the current task to auto-resume after the usage reset, or do you have a different task in mind?"

If the user wants to continue the current task: write a self-contained continuation prompt that describes exactly what to do when usage returns. The resumed session has full conversation memory, so this prompt is the "next message" the model sees.

If the user has a different task (e.g., independent night-time work): ask for the prompt directly.

## Step 2: Ask which files the sandbox needs

Say: "Which files or directories does the resumed session need access to? I'll copy only those into the sandbox — everything else stays untouched, and any edits stay in the sandbox until you explicitly apply them."

## Step 3: Ask about the retry window

Say: "How long should I keep retrying if usage stays unavailable? Default is 24h. Type a number of hours, or 'default'."

## Step 4: Queue and arm

```bash
claude-autoresumer queue add \
  --prompt "<continuation prompt>" \
  --model claude-opus-4-7 \
  --cwd "<absolute path>" \
  --file "<file or dir>" --file "<another>" \
  --max-retry-hours <hours>

claude-autoresumer start
claude-autoresumer status
```

Show the user the `status` output so they can confirm the job is queued AND that the heartbeat shows a recent `Last tick:` line — that's the proof the daemon actually ran, not just installed.

## Final message to user

"Pinned. The daemon ticked once inline so you'll see a fresh `Last tick:` line in `status` — that's how you confirm it's armed without tailing logs. It then ticks every 10 min via launchd, and if the failure error carries the exact reset time, it skips probing until that moment to save calls. When the limit clears it resumes the same Claude Code session — full reasoning, decisions, and partial-work context preserved. Use `claude-autoresumer status` to monitor; `workspaces diff <job_id>` and `workspaces apply <job_id>` to review and accept changes. Keep the laptop plugged in and awake — LaunchAgents pause when macOS sleeps."
