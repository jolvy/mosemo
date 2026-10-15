#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$script_dir/../../../.."
uv run --locked python .codex/skills/verification/scripts/validate_docs.py
uv run --locked python .codex/skills/verification/scripts/validate_architecture.py
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check
uv build
uv run --locked poe test
git diff --check
git diff --cached --check
