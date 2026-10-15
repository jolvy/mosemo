"""Run with: python3 -m unittest discover -s .codex/hooks -p 'test_*.py'."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from protect_main import blocks


class MainPushProtectionTests(unittest.TestCase):
    def test_blocks_forced_main_and_ambiguous_destinations(self):
        commands = [
            "git push --force origin main",
            "git push upstream main --force-with-lease",
            "git push -f origin HEAD:main",
            "git push origin +HEAD:refs/heads/main",
            "git push origin +main",
            "git push --force-with-lease=main:abc upstream HEAD:main",
            "git push --force-with-lease origin",
            "git push -f",
            "git push --force origin HEAD",
            "git push --force --all origin",
            "git push --mirror upstream",
            "git push origin +refs/heads/*:refs/heads/*",
            "git push --force origin feature main",
            "git push --force origin HEAD:$TARGET",
            "git -C /tmp/repo push -f origin main",
            "git -c push.default=current push -f",
            "/usr/bin/git push upstream +HEAD:main",
            "env LANG=C git push -f origin main",
            "git status && git push origin main -f",
            "git status\ngit push upstream main --force",
            "bash -lc 'git push origin HEAD:main --force'",
            'sh -c "git push upstream +main"',
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(blocks(command))

    def test_allows_normal_pushes_and_explicit_feature_force_pushes(self):
        commands = [
            "git status",
            "git push origin main",
            "git push upstream HEAD:main",
            "git push",
            "git push --force origin feature",
            "git push upstream HEAD:refs/heads/feature --force-with-lease",
            "git push origin +HEAD:feature",
            "git push origin +HEAD:feature main",
            "git push origin main:feature -f",
            "git -C /tmp/repo push --force origin feature",
            "git push --force origin feature && git status",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(blocks(command))

    def test_hook_protocol(self):
        script = Path(__file__).with_name("protect_main.py")
        for field in ("command", "cmd"):
            for command, expected in (
                ("git push -f origin main", 2),
                ("git status", 0),
            ):
                with self.subTest(field=field, command=command):
                    result = subprocess.run(
                        [sys.executable, str(script)],
                        input=json.dumps({"tool_input": {field: command}}),
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)
        result = subprocess.run(
            [sys.executable, str(script)],
            input="{}",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
