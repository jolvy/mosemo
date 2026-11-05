import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_modules_import_without_configuration_environment():
    # The OpenAPI check imports these before its fixture sets the environment.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import mosemo.platform.database.engine, mosemo.entrypoints.api.openapi, mosemo.identity.bootstrap",
        ],
        cwd=ROOT,
        env={},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
