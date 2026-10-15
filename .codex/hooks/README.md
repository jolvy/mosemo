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
