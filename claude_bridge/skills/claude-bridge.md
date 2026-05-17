---
name: claude-bridge
description: >
  Use when the user says "set up overnight continuation", "continue while I sleep",
  or "queue a night job". Also invoke proactively when usage is critically low and
  there is unfinished work. Guides Claude to write a checkpoint, queue jobs, and
  arm the overnight daemon.
---

# claude-bridge Skill

You are helping the user hand off work to an overnight autonomous session.

## Step 1: Confirm intent

Ask: "Before I go, should I queue the current task to continue overnight, or do you have a different job in mind?"

If the user wants to continue the current task, proceed to Step 2.
If the user has a different job, ask for the prompt and skip to Step 4.

## Step 2: Write a checkpoint file

Write a JSON file to `~/.claude-bridge/checkpoint-{timestamp}.json` with this structure:

```json
{
  "prompt": "<clear, self-contained description of exactly what to do next>",
  "cwd": "<absolute path to working directory>",
  "source_files": ["<files and dirs the night session needs>"],
  "model": "claude-opus-4-7",
  "completed_steps": ["<what has been done so far>"],
  "next_step": "<the exact next action>"
}
```

The `prompt` field must be fully self-contained — the night session will have no conversation history.
Include enough context that a fresh Claude session can continue without asking questions.

## Step 3: Ask the user which files to include

Say: "Which files or directories does the night session need? I'll copy only those into the sandbox."

Wait for the answer, then update `source_files` in the checkpoint.

## Step 4: Ask about workflow

Say: "Should the night session use any special workflow? Options: minimal (default), tdd, research, thorough — or describe what you want."

Map the answer to `--workflow <template>` or add to `custom_instructions`.

## Step 5: Ask about self-healing

Say: "If the night session runs out of usage mid-job, should I keep retrying? Options: always, 8h (default), or no."

## Step 6: Ask about additional jobs

Say: "Want to queue any follow-up jobs? For example: 'refactor with Opus', 'run security review', or 'write tests'. Say 'done' when finished."

## Step 7: Arm the daemon

Run these commands in sequence:

```bash
claude-bridge queue add --resume --checkpoint ~/.claude-bridge/checkpoint-{timestamp}.json \
  --workflow {template} --self-heal {policy}
```

For any additional jobs the user described:

```bash
claude-bridge queue add --prompt "..." --model claude-opus-4-7 \
  --files "..." --workflow {template}
```

Then arm the daemon:

```bash
claude-bridge start --self-heal {policy}
claude-bridge status
```

Show the user the output of `claude-bridge status` so they can confirm the queue before going to bed.

## Final message to user

"The daemon is armed. I'll probe for usage every 10 minutes and continue your work automatically. Check `claude-bridge status` in the morning, then `claude-bridge diff <job_id>` and `claude-bridge apply <job_id>` to review and accept the changes. Good night."
