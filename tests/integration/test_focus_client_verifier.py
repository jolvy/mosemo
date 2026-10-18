import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent


def test_focus_client_verifier_provisions_its_own_kafka_and_processes_publications(
    tmp_path,
):
    client = tmp_path / "client"
    client.mkdir()
    (client / "Package.swift").touch()
    executable = tmp_path / "swift"
    # Substitute only the Swift process; API, Kafka, and PostgreSQL are real.
    executable.write_text(
        f"#!{sys.executable}\n"
        + dedent("""\
        import json
        import os
        import urllib.request
        from pathlib import Path
        from uuid import uuid4

        fixture = json.loads(Path(os.environ['MOSEMO_FOCUS_E2E_FIXTURE']).read_text())
        def post(path, body, expected):
            request = urllib.request.Request(
                fixture['url'] + '/api/v1' + path,
                json.dumps(body).encode(),
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + fixture['token']},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.status == expected

        session_id = str(uuid4())
        post('/focus-sessions', {
            'sessionId': session_id,
            'deviceId': fixture['deviceID'],
            'startedAt': '2026-10-18T00:00:00Z',
            'targetSeconds': 120,
        }, 201)
        batch = {
            'batchId': str(uuid4()),
            'deviceId': fixture['deviceID'],
            'records': [{
                'eventId': str(uuid4()), 'sequence': 0,
                'recordType': 'activity_observation',
                'observedAt': '2026-10-18T00:00:10Z',
                'timezoneId': 'Asia/Seoul', 'utcOffsetMinutes': 540,
                'context': {'kind': 'opaque'}, 'focusSessionId': session_id,
            }],
        }
        post('/activities/batches', batch, 202)
        post('/activities/batches', batch, 202)
        """)
    )
    executable.chmod(0o700)
    env = os.environ.copy()
    env.update(
        PATH=str(tmp_path) + os.pathsep + env["PATH"],
        BATCH_ENABLED="false",
        BATCH_BOOTSTRAP_SERVERS="127.0.0.1:1",
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/verify_focus_session_client.py",
            "--client-root",
            str(client),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "PostgreSQL verification: activity record links to focus session"
        in result.stdout
    )
