"""Run Alembic without placing the database secret in process arguments."""

from __future__ import annotations

import os

from alembic.config import Config

from alembic import command


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    configuration = Config("alembic.ini")
    configuration.attributes["database_url"] = database_url
    command.upgrade(configuration, "head")


if __name__ == "__main__":
    main()
