import json
import logging

from app.core.logging import JsonFormatter


def test_json_formatter_redacts_common_secret_shapes() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=(
            "failed postgresql+asyncpg://user:database-password@db:5432/app "
            "using sk-or-v1-secret-token Authorization: Bearer arbitrary-value "
            "OPENROUTER_API_KEY=another-value"
        ),
        args=(),
        exc_info=None,
    )

    payload = json.loads(formatter.format(record))

    assert "database-password" not in payload["message"]
    assert "sk-or-v1-secret-token" not in payload["message"]
    assert "arbitrary-value" not in payload["message"]
    assert "another-value" not in payload["message"]
    assert payload["message"].count("[REDACTED]") == 4
