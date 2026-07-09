"""
One-time data migration: SQLite -> PostgreSQL.

WHY THIS EXISTS
----------------
The app currently defaults to a local SQLite file for its database. On
Render (and on any ephemeral/serverless host), the local filesystem is wiped
on every redeploy/restart — meaning every team, task, chat message, and
knowledge item can be lost the next time you push a change. This script
moves your existing data, once, into a real Postgres database so that
stops being true. It does not change how the app talks to whichever
database DATABASE_URL points at — see app.py's _normalize_database_url.

HOW TO RUN THIS SAFELY
-----------------------
1. Provision a Postgres database (e.g. Render's Postgres add-on) and get
   its connection string.
2. Make a copy of your current SQLite file somewhere safe. This script
   only reads from the SQLite file, but always keep a backup regardless.
3. Stop the app (or at least stop writes) so no new rows are created in
   SQLite while this runs — this is a one-shot copy, not a live sync.
4. Run:
       python scripts/migrate_sqlite_to_postgres.py \\
           --sqlite ./ai_team_brain.db \\
           --postgres "postgresql://user:pass@host:5432/dbname"

   (Or set env vars SQLITE_PATH and DATABASE_URL instead of passing flags —
   DATABASE_URL is picked up automatically if you run this with the same
   .env the app uses.)

5. Read the summary at the end. It tells you per-table row counts on both
   sides and refuses to report success unless every table matches exactly.
6. Only after that, point the app's real DATABASE_URL at Postgres and
   restart it. Keep the SQLite file around as a backup for a while before
   deleting it.

SAFETY
------
- This script REFUSES to run if the destination Postgres tables already
  have any rows in them, specifically to prevent accidentally doubling up
  data if it's run twice. If you need to re-run it (e.g. after fixing an
  error), truncate the destination tables first.
- It never modifies or deletes anything in the source SQLite file.
- It copies through the app's own SQLAlchemy model definitions
  (models/database.py), so the destination schema is guaranteed to match
  exactly what the running app expects — nothing hand-copied to drift out
  of sync.
"""

import argparse
import os
import sys

# Allow running as `python scripts/migrate_sqlite_to_postgres.py` from the
# backend/ directory.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, select, insert, func

import models.database as models_module

METADATA = models_module.db.metadata

# Every table only references tables that appear earlier in this list —
# this is a foreign-key-safe insert order (parents before children).
# Mirrors the same dependency order api/auth/routes.py's delete_team()
# already deletes in, just reversed.
TABLE_ORDER = [
    "users",
    "teams",
    "team_members",
    "chat_rooms",
    "chat_messages",
    "tasks",
    "knowledge_items",
    "activity_logs",
    "notifications",
    "daily_summaries",
]


def normalize_pg_url(url: str) -> str:
    """Render (and most managed Postgres providers) hand out connection
    strings starting with 'postgres://', which SQLAlchemy 1.4+/2.0 rejects —
    it requires 'postgresql://'. Mirrors app.py's _normalize_database_url."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def redact(url: str) -> str:
    """For printing to the terminal without leaking credentials."""
    return url.split("@")[-1] if "@" in url else url


def main():
    parser = argparse.ArgumentParser(description="Migrate TeamPulse data from SQLite to PostgreSQL")
    parser.add_argument("--sqlite", default=os.getenv("SQLITE_PATH", "./ai_team_brain.db"),
                         help="Path to the existing SQLite database file")
    parser.add_argument("--postgres", default=os.getenv("DATABASE_URL"),
                         help="Destination Postgres connection string")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()

    if not args.postgres:
        print("ERROR: no Postgres URL given. Pass --postgres or set DATABASE_URL.")
        sys.exit(1)

    if not os.path.exists(args.sqlite):
        print(f"ERROR: SQLite file not found at {args.sqlite}")
        sys.exit(1)

    pg_url = normalize_pg_url(args.postgres)
    if not pg_url.startswith("postgresql://"):
        print(f"ERROR: --postgres does not look like a Postgres URL: {redact(pg_url)}")
        sys.exit(1)

    sqlite_url = f"sqlite:///{os.path.abspath(args.sqlite)}"

    print(f"Source (SQLite):      {sqlite_url}")
    print(f"Destination (Postgres): {redact(pg_url)}")

    if not args.yes:
        confirm = input("\nThis will COPY data into the destination database. Continue? [y/N] ")
        if confirm.strip().lower() != "y":
            print("Aborted.")
            sys.exit(0)

    src_engine = create_engine(sqlite_url)
    dst_engine = create_engine(pg_url)

    print("\nCreating schema on destination (Postgres) if not already present...")
    METADATA.create_all(dst_engine)

    # Refuse to run if the destination already has data, to prevent
    # accidentally double-inserting on a re-run.
    with dst_engine.connect() as dst_conn:
        for table_name in TABLE_ORDER:
            table = METADATA.tables[table_name]
            count = dst_conn.execute(select(func.count()).select_from(table)).scalar()
            if count and count > 0:
                print(f"\nERROR: destination table '{table_name}' already has {count} row(s).")
                print("Refusing to run to avoid duplicating data. If you intend to")
                print("re-run this migration, truncate the destination tables first.")
                sys.exit(1)

    print("Destination tables are empty — safe to proceed.\n")

    src_counts = {}
    dst_counts = {}

    with src_engine.connect() as src_conn, dst_engine.connect() as dst_conn:
        for table_name in TABLE_ORDER:
            table = METADATA.tables[table_name]
            rows = src_conn.execute(select(table)).mappings().all()
            src_counts[table_name] = len(rows)

            if rows:
                batch_size = 500
                for i in range(0, len(rows), batch_size):
                    batch = [dict(r) for r in rows[i:i + batch_size]]
                    dst_conn.execute(insert(table), batch)
            dst_conn.commit()

            dst_count = dst_conn.execute(select(func.count()).select_from(table)).scalar()
            dst_counts[table_name] = dst_count

            status = "OK" if dst_count == len(rows) else "MISMATCH"
            print(f"  {table_name:<18} {len(rows):>6} rows copied  ->  {dst_count:>6} in destination   [{status}]")

    print("\n=== Summary ===")
    all_ok = True
    for table_name in TABLE_ORDER:
        if src_counts[table_name] != dst_counts[table_name]:
            all_ok = False
            print(f"  MISMATCH in {table_name}: source={src_counts[table_name]} destination={dst_counts[table_name]}")

    if all_ok:
        print("All tables match. Migration completed successfully.")
        print("\nNext steps:")
        print("  1. Point DATABASE_URL at this Postgres database in your real environment.")
        print("  2. Restart the app.")
        print("  3. Spot-check the app against real data before decommissioning the SQLite file.")
        print("  4. Keep the SQLite file as a backup for a while before deleting it.")
    else:
        print("\nSome tables did not match. DO NOT switch the app over yet.")
        print("Investigate the mismatch before proceeding.")
        sys.exit(1)


if __name__ == "__main__":
    main()
