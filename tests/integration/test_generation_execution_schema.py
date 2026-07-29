"""Metadata-only execution schema constraints."""

from pathlib import Path

from sqlalchemy import JSON, LargeBinary, String, Text

from car_wrap.db.models import GenerationAttempt, GenerationJob


def test_attempt_schema_contains_only_bounded_scalar_metadata() -> None:
    forbidden_names = {
        "payload",
        "body",
        "media",
        "bytes",
        "url",
        "authorization",
        "headers",
    }
    for column in GenerationAttempt.__table__.columns:
        assert not isinstance(column.type, (JSON, LargeBinary, Text))
        assert not any(name in column.name for name in forbidden_names)
        if isinstance(column.type, String):
            assert column.type.length is not None
    assert "result_message_id" in GenerationJob.__table__.columns


def test_execution_migration_has_no_private_payload_column() -> None:
    migration = (
        Path(__file__).parents[2] / "alembic/versions/0004_generation_execution.py"
    ).read_text(encoding="utf-8")
    assert not any(
        fragment in migration
        for fragment in (
            "LargeBinary",
            "sa.JSON",
            "sa.Text",
            "data_url",
            "authorization",
            "provider_body",
        )
    )
