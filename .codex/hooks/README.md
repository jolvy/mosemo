# Main branch push protection

`.codex/hooks.json` registers a Codex `PreToolUse` shell hook. It applies to
all remotes, including `origin`, `upstream`, and URL destinations. Normal pushes
to main remain allowed.

The hook blocks forced updates to `main` / `refs/heads/main` using `--force`,
`--force-with-lease`, `-f`, or a leading `+` in a refspec. Mirror pushes are
blocked because they can force-update main. Forced pushes with omitted,
symbolic, wildcard, or otherwise uncertain destinations are also blocked.
Explicit non-main branch destinations remain available for forced pushes.

After restarting Codex, review and trust the project hook using `/hooks` in
the CLI (or the hook review interface provided by your client). Until trusted,
the hook is skipped. This does not change GitHub settings or install Git hooks.

This is an accident-prevention guardrail for visible shell commands, not a
complete shell interpreter or remote enforcement boundary. Git aliases,
commands constructed dynamically, scripts that push internally, other tools,
and input sent to an existing terminal can bypass inspection. GitHub branch
protection is needed to enforce protection independently of Codex.

Run local checks without pushing anything:

```sh
python3 -m unittest discover -s .codex/hooks -p 'test_*.py'
```

# Code style feedback hooks

Claude Code (`.claude/settings.json`) and Codex (`.codex/hooks.json`) run the
same two scripts with `uv run --locked python` from the repository root. Only
input parsing differs between the tools.

## After each edit: `post_edit_feedback.py`

- Event: `PostToolUse`. Claude Code matcher `Edit|Write` (path from
  `tool_input.file_path`), Codex matcher `^apply_patch$` (paths from the
  `*** Add File: `, `*** Update File: `, and `*** Move to: ` lines of
  `tool_input.command`, relative to the hook input `cwd`; deleted files are
  skipped). Timeout 10 seconds.
- Targets only existing `.py` files inside the repository. It runs
  `ruff format` and then `ruff check` on them.
- When violations remain, it writes one JSON line to stdout with
  `hookSpecificOutput.additionalContext` listing them in Ruff concise format.
  `F401` and `F841` are not reported here because they appear while code is
  being written; the stop hook judges them.
- It never blocks. Invalid input, command failures, and timeouts are logged as
  one stderr line, with no stdout, and the hook exits 0.

## Before finishing: `stop_gate.py`

- Event: `Stop`. Timeout 300 seconds; the script stops itself after 240
  seconds so that the tool does not treat a timeout as a pass. `SubagentStop`
  is not registered: the parent session's `Stop` judges the whole work tree.
- Changed paths come from
  `git status --porcelain=v1 -z --untracked-files=all`, so files edited by
  shell commands, untracked files in new directories, deletions, and both
  sides of renames are included. Without a changed `.py` path it exits 0.
- It formats the changed `.py` files that exist, then skips checking when the
  change fingerprint (every changed path and its content hash) equals the last
  pass or the last block of the same session.
- Style checks: `ruff check --output-format concise .`, the fixed complexipy
  command, `validate_conventions.py`, `validate_architecture.py`, and
  `ty check --output-format concise`. If any fails, tests are not run.
- Tests: `uv run --locked --env-file .env.example pytest -q --tb=short -p
  no:cacheprovider tests/unit` plus component tests chosen by path:
  `src/mosemo/` top-level files, `src/mosemo/infra/`, `tests/` top-level
  files, and non-`test_*.py` files under `tests/component/` select all of
  `tests/component`; `src/mosemo/P/...` selects `tests/component/test_P_api.py`
  when it exists; a changed `tests/component/test_*.py` selects itself.
- To block it writes to stderr and exits 2. The first line tells the model
  to fix the violations, that repository rules take precedence over naming or
  shape instructions from users and Issues, and to report the cause and a
  recommended action instead of changing code when an exception to a rule is
  needed. Each failed check follows with its command and up to 80 output
  lines. Nothing is written to stdout.
- Retry state is stored per `session_id` in
  `$(git rev-parse --git-dir)/mosemo-agent-stop-gate.json`, outside the work
  tree. The same failure (check names and outputs without line, column, or
  timing differences) is blocked at most three times; the fourth stop passes.
  Any pass clears the block record. Unreadable state counts as the first
  block.
- Hook failures (exceptions, missing commands, the internal time limit) block
  with `검사기 실행 실패` and follow the same retry rule.

## Trust and limits

- Claude Code reads `.claude/settings.json` when a session starts.
- Codex skips project hooks until they are trusted. After `.codex/hooks.json`
  changes, restart Codex and trust the hooks again with `/hooks`.
- The hooks can be bypassed: untrusted or disabled hooks do not run. CI remains
  the final enforcement.
- Test selection follows path rules only. For example, `activities` changes do
  not run `test_batch_consumer.py`; pre-push and CI cover the rest.
- The stop hook formats every changed `.py` file in the work tree, including
  changes unrelated to the current task.

Run the hook tests:

```sh
uv run --locked pytest -q tests/unit/harness/test_agent_hooks.py
```
