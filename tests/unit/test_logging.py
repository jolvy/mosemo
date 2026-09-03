import json
import logging
import sys

from mosemo.logging import JsonFormatter, SensitiveDataFilter, configure_logging


def test_json_formatter_emits_expected_fields(monkeypatch) -> None:
    monkeypatch.setenv("MOSEMO_ENV", "test")
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="mosemo.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="test_completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "request-123"
    record.request_url = "https://api.example.com/callback?next=home"
    record.activity_url = "https://activity.example.com/private"
    record.authorization = "Bearer secret"

    payload = json.loads(formatter.format(record))

    assert payload == {
        "timestamp": payload["timestamp"],
        "service": "mosemo",
        "environment": "test",
        "logger": "mosemo.test",
        "level": "INFO",
        "event": "test_completed",
        "request_id": "request-123",
        "request_url": "https://api.example.com/callback?next=home",
    }
    assert payload["timestamp"].endswith("Z")


def test_json_formatter_excludes_exception_message() -> None:
    formatter = JsonFormatter()

    try:
        raise ValueError("secret exception detail")
    except ValueError:
        exc_info = sys.exc_info()
        exception_traceback = exc_info[2]
        assert exception_traceback is not None
        exception_line = exception_traceback.tb_lineno
        record = logging.LogRecord(
            name="mosemo.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="operation_failed",
            args=(),
            exc_info=exc_info,
        )

    output = formatter.format(record)
    payload = json.loads(output)

    assert "secret exception detail" not in output
    assert payload["exception"] == {
        "type": "ValueError",
        "file": "test_logging.py",
        "function": "test_json_formatter_excludes_exception_message",
        "line": exception_line,
    }


def test_sensitive_data_filter_redacts_copy_including_message_args() -> None:
    record = logging.LogRecord(
        name="mosemo.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="request failed at %s with %s and user_id=%s",
        args=(
            "https://example.com/callback?code=secret",
            "Bearer token-value",
            "account-123",
        ),
        exc_info=None,
    )
    record.request_url = "https://example.com/callback?state=secret&next=home"
    record.activity_url = "https://activity.example.com/private"

    sanitized_record = SensitiveDataFilter().filter(record)
    output = JsonFormatter().format(sanitized_record)

    assert sanitized_record is not record
    assert "code=secret" in record.getMessage()
    assert "https://example.com/callback?" in output
    assert "code=secret" not in output
    assert "state=secret" not in output
    assert "token-value" not in output
    assert "account-123" not in output
    assert sanitized_record.args == ()
    assert json.loads(output)["request_url"] == (
        "https://example.com/callback?[REDACTED]&next=home"
    )
    assert "activity.example.com" not in output


def test_configure_logging_uses_stdout_without_file_handlers(
    capsys,
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    configure_logging()

    logging.getLogger("mosemo.test").info(
        "configured request to %s with %s",
        "https://example.com/callback?code=secret",
        "Bearer token-value",
        extra={"request_url": "https://example.com/callback?state=secret&next=home"},
    )
    logging.getLogger("uvicorn.error").warning("server_warning")
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1:1234",
        "GET",
        "/access-only?code=secret",
        "1.1",
        200,
    )

    captured = capsys.readouterr()
    payloads = [json.loads(line) for line in captured.out.splitlines()]

    assert payloads[0]["event"] == (
        "configured request to https://example.com/callback?[REDACTED] "
        "with Bearer [REDACTED]"
    )
    assert payloads[0]["request_url"] == (
        "https://example.com/callback?[REDACTED]&next=home"
    )
    assert payloads[1]["event"] == "server_warning"
    assert "access-only" not in captured.out
    assert "code=secret" not in captured.out
    assert "state=secret" not in captured.out
    assert "token-value" not in captured.out
    assert captured.err == ""
    assert logging.getLogger().level == logging.WARNING
    assert logging.getLogger("mosemo").level == logging.INFO
    assert logging.getLogger("uvicorn").level == logging.INFO
    assert logging.getLogger("uvicorn.access").handlers == []
    assert not logging.getLogger("uvicorn.access").propagate
    assert not any(
        isinstance(handler, logging.FileHandler)
        for logger in (
            logging.getLogger(),
            logging.getLogger("mosemo"),
            logging.getLogger("uvicorn"),
        )
        for handler in logger.handlers
    )
