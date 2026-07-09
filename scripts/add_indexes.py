"""
Add the indexes now declared in models/database.py to an EXISTING database.

WHY THIS EXISTS
----------------
SQLAlchemy's db.create_all() only creates tables/indexes that don't already
exist — it will NOT retroactively add a new index to a table that's already
there. So if your database was created before this change (including via
the Phase 3 migration script), the new `index=True` columns in the models
won't actually get indexed until you run this once.

If you're setting up a brand new database from scratch, you don't need
this at all — db.create_all() already includes these indexes automatically.

Safe to run multiple times: every statement is CREATE INDEX IF NOT EXISTS.
Works against either SQLite or Postgres (whatever DATABASE_URL points at).

Usage:
    python scripts/add_indexes.py
    (reads DATABASE_URL from the environment/.env, same as the app does)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, text

# (index_name, table, column) — mirrors the index=True columns in
# models/database.py exactly.
INDEXES = [
    ("idx_team_members_team_id",  "team_members",    "team_id"),
    ("idx_team_members_user_id",  "team_members",    "user_id"),
    ("idx_tasks_team_id",         "tasks",           "team_id"),
    ("idx_tasks_assignee_id",     "tasks",           "assignee_id"),
    ("idx_tasks_creator_id",      "tasks",           "creator_id"),
    ("idx_chat_rooms_team_id",    "chat_rooms",      "team_id"),
    ("idx_chat_messages_room_id", "chat_messages",   "room_id"),
    ("idx_chat_messages_user_id", "chat_messages",   "user_id"),
    ("idx_knowledge_items_team_id","knowledge_items","team_id"),
    ("idx_activity_logs_team_id", "activity_logs",   "team_id"),
    ("idx_activity_logs_user_id", "activity_logs",   "user_id"),
    ("idx_notifications_user_id", "notifications",   "user_id"),
    ("idx_notifications_team_id", "notifications",   "team_id"),
    ("idx_daily_summaries_team_id","daily_summaries","team_id"),
]


def normalize_pg_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def main():
    db_url = normalize_pg_url(os.getenv("DATABASE_URL", "sqlite:///ai_team_brain.db"))
    redacted = db_url.split("@")[-1] if "@" in db_url else db_url
    print(f"Adding indexes to: {redacted}")

    engine = create_engine(db_url)
    with engine.connect() as conn:
        for name, table, column in INDEXES:
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"))
            conn.commit()
            print(f"  OK: {name} on {table}({column})")

    print("\nDone. All indexes present (existing ones were left untouched).")


if __name__ == "__main__":
    main()
