"""Database commands.

    python -m abtest.db migrate    # apply pending migrations, then bootstrap

Run it from the repository root (`make migrate` does), or from a directory that has
db/migrations, as the Docker image does.
"""

import argparse
from pathlib import Path

import psycopg

from abtest.config import MigrateSettings
from abtest.db.bootstrap import bootstrap
from abtest.db.migrate import migrate


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m abtest.db")
    commands = parser.add_subparsers(dest="command", required=True)
    migrate_parser = commands.add_parser("migrate", help="apply migrations, then bootstrap")
    migrate_parser.add_argument("--dir", type=Path, default=Path("db/migrations"))
    args = parser.parse_args()

    settings = MigrateSettings()  # values come from the environment
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        applied = migrate(conn, args.dir)
        bootstrap(
            conn,
            settings.abtest_client_key.get_secret_value(),
            settings.abtest_server_key.get_secret_value(),
        )
    print(f"applied {len(applied)} migration(s): {', '.join(applied) or 'none pending'}")


if __name__ == "__main__":
    main()
