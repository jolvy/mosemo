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
  seconds so that the tool does not treat a timeout as a pass. `stop_gate.py`
  is not registered for `SubagentStop`: the parent session's `Stop` judges the
  whole work tree. Claude Code registers only `review_metrics.py` there (see
  "Review lines" below).
- Changed paths come from
  `git status --porcelain=v1 -z --untracked-files=all`, so files edited by
  shell commands, untracked files in new directories, deletions, and both
  sides of renames are included. Without a changed `.py` path it exits 0.
- It formats the changed `.py` files that exist, then skips checking when the
  change fingerprint (every changed path and its content hash) equals the last
  pass or the last block of the same session.
- Style checks: `ruff check --output-format concise .`, the fixed complexipy
  command, `validate_conventions.py`, `validate_architecture.py`,
  `ty check --output-format concise`, and `poe lint-imports`. If any fails,
  tests are not run.
- Tests: `uv run --locked --env-file .env.example pytest -q --tb=short -p
  no:cacheprovider -m "not requires_db and not requires_kafka" tests/unit`
  plus integration tests chosen by path, so tests that need Docker containers
  are deselected: `src/mosemo/` top-level files,
  `src/mosemo/{shared,platform,entrypoints}/`, `tests/` top-level files, files
  under `tests/support/`, and non-`test_*.py` files under `tests/integration/`
  select all of `tests/integration`; `src/mosemo/P/...` selects
  `tests/integration/P` when it exists; a changed `tests/integration/**/test_*.py`
  selects itself. Only existing paths are kept, and paths are compared by
  their parts, so `tests/unit/support/` does not count as `tests/support/`.
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

### Metrics log

- Every stop, every edit whose `ruff check` finished, every finished review,
  and every full verification run
  (`.codex/skills/verification/scripts/validate.py`) appends one JSON line to
  `MOSEMO_METRICS_LOG` when set, otherwise to
  `$(git rev-parse --git-common-dir)/mosemo-agent-metrics.jsonl`, outside the
  work tree and shared by all worktrees. Each line is written with one `write`
  in append mode (`stop_metrics.append_metrics`) so that concurrent sessions
  do not interleave.
- Common fields, in this order, on every new line:
  - `at`: UTC time, `%Y-%m-%dT%H:%M:%SZ`.
  - `event`: `edit` (`post_edit_feedback.py`), `stop` (`stop_gate.py`),
    `review` (`review_metrics.py`), or `verify` (`validate.py`).
  - `session`: hook input `session_id`, otherwise an empty string. Verify
    lines use `CLAUDE_CODE_SESSION_ID`, otherwise `null`.
  - `agent`: `MOSEMO_AGENT` (`claude` or `codex` in the hook commands),
    otherwise `unknown`. Verify lines use `claude` when
    `CLAUDE_CODE_SESSION_ID` is set, otherwise `unknown`.
  - `model`: the last `"model":"claude-…"` value in the transcript, otherwise
    `null`. Verify lines always use `null`. Stop lines read the whole
    transcript; edit lines read only its last 1 MiB.
  - `harness_rev`, `harness_dirty`: The last commit
    (`git log -1 --format=%h`, `null` when unknown) and whether
    `git status --porcelain` shows uncommitted changes, both limited to
    `.codex/`, `.claude/`, `AGENTS.md`, `CLAUDE.md`, `src/mosemo/AGENTS.md`,
    `src/mosemo/CLAUDE.md`, `tests/AGENTS.md`, `tests/CLAUDE.md`,
    `tests/unit/harness/`, `docs/code-style-rubric.md`, and `docs/adr/`.
    `harness_dirty` is always a boolean: when Git fails or times out it is
    `true`, so that lines of unknown state are not mixed with a clean
    revision.
  - `branch`: `git branch --show-current`; `null` on a detached HEAD (empty
    output) or when Git fails.
- Stop lines add:
  - `skills`: Skill names from the transcript, deduplicated in order of
    appearance: `Skill` tool calls (`input.skill`), `<command-name>` values in
    user message text (without the leading `/`), and `Read` calls whose
    `file_path` ends with `.codex/skills/<name>/SKILL.md` or
    `.claude/skills/<name>/SKILL.md`.
  - `result`: `skip` (no changed `.py`), `cached` (same fingerprint as the last
    pass or block), `allow`, `block`, `capped` (the same failure passed after
    `MAX_BLOCKS` blocks), or `error` (blocked because a check could not run).
  - `session_blocks`: total blocks in the session, stored as `blocks` in the
    session record of the state file. It is not reset when the failure
    changes or a stop passes.
  - `failed`: names of failed checks.
  - `rules`: violation counts by Ruff rule code (syntax errors under Ruff's
    `invalid-syntax` label) and `validate_conventions` `CS-P..` ID. Other
    checks (`validate_architecture`, `ty check`, `complexipy`, `pytest`, and
    hook failures) are keyed by check name and count their failure lines.
- Edit lines add `rules`: counts by Ruff rule code of the violations left
  after the edit hook's `ruff check`, `{}` when none remain. `F401` and `F841`
  never appear because the edit hook ignores them. An edit line is written
  only when the edit has an existing `.py` target and `ruff check` finished;
  edits without a `.py` target and Ruff failures or timeouts write nothing,
  so the pass rate counts only finished Python checks.
- Verify lines add:
  - `ok`, `failed`, `not_run`: the same values as the `VERIFY_RESULT` line.
  - `durations`: seconds (float) by check name for the checks that ran.
  A recording failure leaves one line on stderr and changes neither the exit
  code nor `VERIFY_RESULT`.
- Time: the edit hook keeps the 10-second hook timeout. Ruff gets 7 seconds,
  and recording (Git queries for the log path, harness revision, harness
  changes, and branch; the transcript tail; the write) gets a 1.5-second
  deadline. Fields past the deadline take their unknown value and the line is
  still written.
- The transcript (`transcript_path`, JSONL) is read line by line and
  unparsable lines are skipped. Without a readable path, `skills` is `[]` and
  `model` is `null`. The parser targets the Claude Code format; Codex runs are
  told apart only by `agent`.
- Logging never changes the decision. The hooks decide their output and exit
  code first and then call one recording function that swallows failures: a
  failure prints one stderr line starting with `지표 로그 기록 실패`, the
  edit hook still writes its violation notice and exits 0, and the stop hook
  keeps its exit code.
- The parsers, the common fields, and the writer live in `stop_metrics.py`;
  review lines live in `review_metrics.py`;
  the hooks pass their Git query as a function. Their tests and the edit line
  tests (`tests/unit/harness/test_stop_metrics.py`,
  `tests/unit/harness/test_edit_metrics.py`,
  `tests/unit/harness/test_review_metrics.py`) use no Git or subprocess and do
  not run the hooks' `main`.

### Review lines

- The three review agents (`code-reviewer`, `domain-reviewer`,
  `security-auditor`) are told in their `developer_instructions` to start the
  report, and the final response even when the result is saved to a file,
  with exactly `REVIEW high=N medium=N low=N`. The sentence is identical in
  the three `.codex/agents/*.toml` files.
- The report's first non-empty line, stripped, is matched against
  `^REVIEW high=(\d+) medium=(\d+) low=(\d+)$`. Review lines add:
  - `reviewer`: the hook input `agent_type`.
  - `findings`: `{"high": n, "medium": n, "low": n}` when the line matches,
    otherwise `null`.
  - `format_ok`: whether the line matches.
  - `via`: `subagent_stop` or `agent_stop`.
- `SubagentStop` (Claude Code, `.claude/settings.json`, timeout 10 seconds):
  `MOSEMO_AGENT=claude uv run --locked python .codex/hooks/review_metrics.py`.
  An `agent_type` other than the three reviewers writes nothing, so internal
  agents that also fire `SubagentStop` are skipped. The report is
  `last_assistant_message`, otherwise the last assistant message text of
  `agent_transcript_path`. `model` comes from that transcript's last 1 MiB.
  It never blocks: exit 0, no stdout, failures as one stderr line.
- `--agent` sessions: after writing its stop line, `stop_gate.py` writes a
  review line (`via` `agent_stop`) when `agent_type` is a reviewer and the
  stop exits 0. A blocked stop writes none, because the agent keeps working
  and stops again. The report is `last_assistant_message`, otherwise the
  joined `text` blocks of the last assistant message (same `message.id`) in
  the last 1 MiB of `transcript_path`. The stop decision does not change.
  Aggregation counts only the last `agent_stop` line per session and
  reviewer.
- Codex `SubagentStop` is not registered yet.

## Trust and limits

- Claude Code reads `.claude/settings.json` when a session starts.
- Codex skips project hooks until they are trusted. After `.codex/hooks.json`
  changes, restart Codex and trust the hooks again with `/hooks`.
- The hooks can be bypassed: untrusted or disabled hooks do not run. CI remains
  the final enforcement.
- Test selection follows path rules only. For example, `observation` changes do
  not run `tests/integration/entrypoints/test_batch_consumer.py`; pre-push and
  CI cover the rest.
- The stop hook formats every changed `.py` file in the work tree, including
  changes unrelated to the current task.

The repository keeps no behavior tests for these hooks (Plan #109, decision
S7). The removed tests ran inside the pre-commit `harness-tests` stage, inherited
the `GIT_DIR` and related variables that Git passes to hooks, and wrote test
settings into the shared `.git/config`. If you add hook tests again, unset every
variable listed by `git rev-parse --local-env-vars` before a test runs Git.
