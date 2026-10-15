"""Codex shell guardrail against forced updates to any remote's main branch."""

import json
import shlex
import sys
from pathlib import PurePosixPath


def unsafe_push(arguments: list[str]) -> bool:
    force = False
    positional: list[str] = []
    uncertain = False
    options = True
    skip_value = False
    for argument in arguments:
        if skip_value:
            skip_value = False
            continue
        if options and argument == "--":
            options = False
        elif options and argument.startswith("-"):
            name = argument.split("=", 1)[0]
            if name == "--mirror":
                return True
            if (
                name in {"--force", "--force-with-lease", "--force-if-includes"}
                or not argument.startswith("--")
                and "f" in argument[1:]
            ):
                force = True
            elif name in {"--repo", "--receive-pack", "--exec", "--push-option", "-o"}:
                skip_value = "=" not in argument
                uncertain = True
            elif name not in {
                "--verbose",
                "--quiet",
                "--porcelain",
                "--progress",
                "--dry-run",
                "--atomic",
                "--set-upstream",
                "--no-verify",
                "--follow-tags",
                "--signed",
                "--no-signed",
                "-v",
                "-q",
                "-u",
                "-n",
            }:
                uncertain = True
        else:
            positional.append(argument)

    refspecs = positional[1:]  # First positional argument is the remote / URL.
    if not force and not any(ref.startswith("+") for ref in refspecs):
        return False
    if uncertain or not refspecs:
        return True
    for refspec in refspecs:
        if not force and not refspec.startswith("+"):
            continue
        destination = refspec.lstrip("+").rsplit(":", 1)[-1]
        if destination in {"main", "refs/heads/main", ""}:
            return True
        if any(character in destination for character in "*$`?[]{}\\"):
            return True
        # HEAD and symbolic revisions are not explicit destination branch names.
        if destination == "HEAD" or any(c in destination for c in "~^"):
            return True
    return False


def blocks(command: str, depth: int = 0) -> bool:
    if depth > 8:
        return True
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return True
    separators = {";", "&&", "||", "|", "&", "(", ")", "\n"}
    for index, token in enumerate(tokens):
        # Inspect quoted shell scripts too: bash -c 'git push ...', etc.
        if " " in token and "git" in token and blocks(token, depth + 1):
            return True
        if PurePosixPath(token).name != "git":
            continue
        end = index + 1
        while end < len(tokens) and tokens[end] not in separators:
            end += 1
        arguments = tokens[index + 1 : end]
        # Skip Git's global options, including git -C <path> and git -c <setting>.
        cursor = 0
        while cursor < len(arguments) and arguments[cursor].startswith("-"):
            option = arguments[cursor]
            cursor += 2 if option in {"-C", "-c", "--git-dir", "--work-tree"} else 1
        if (
            cursor < len(arguments)
            and arguments[cursor] == "push"
            and unsafe_push(arguments[cursor + 1 :])
        ):
            return True
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool_input = payload["tool_input"]
        command = tool_input.get("command", tool_input.get("cmd"))
        if not isinstance(command, str):
            raise TypeError("Missing shell command")
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"Cannot inspect shell command: {error}", file=sys.stderr)
        return 2
    if blocks(command):
        print(
            "Blocked: forced push to main, mirror push, or an ambiguous forced "
            "push. Use a normal push, or specify a non-main destination branch.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
