---
name: Open Terminal work
description: Inspect files, edit projects, run code, and manage long commands exclusively inside Open Terminal.
version: 1
---
# Open Terminal work

Apply this skill for every task involving files, shell commands, code execution,
tests, or processes.

- Open Terminal is the only execution sandbox. Never claim to have executed or
  read anything locally in the voice gateway.
- Inspect before editing: use `list_files`, `grep`, and `read` to understand the
  target and preserve unrelated work.
- Use `write` for complete new files and `replace` for bounded edits.
- Use `execute` for commands and tests. If it returns a running process ID, poll
  it with `process_status`; use offsets to avoid repeating output.
- Use `process_input` only when the running program requires input.
- Use `process_kill` only when stopping that process is part of the user's task
  or is necessary to recover from a failed attempt.
- Report exact paths, tests, and unresolved blockers. Never invent command
  output.
