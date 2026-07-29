"""Compatibility helper for integration tests importing URL validation."""

from urllib.parse import urlsplit


def validate_test_database_url(value: str) -> str:
    """Reject non-PostgreSQL and non-test database targets."""

    parsed = urlsplit(value)
    database_name = parsed.path.lstrip("/")
    if parsed.scheme != "postgresql+psycopg":
        raise ValueError("integration database must use PostgreSQL with Psycopg")
    if parsed.hostname is None or "test" not in database_name.lower():
        raise ValueError("integration database name must contain 'test'")
    return value
