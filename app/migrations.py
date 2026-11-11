"""Small compatibility migrations for existing SQLite and PostgreSQL databases.

For larger production schema changes, a versioned Alembic migration history is
recommended. These compatibility steps keep this starter upgradeable in place.
"""
import re
from sqlalchemy import inspect, text
from .database import engine


def run_compatibility_migrations() -> None:
    dialect = engine.dialect.name
    if dialect not in ("sqlite", "postgresql"):
        return
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("users")}
    datetime_type = "DATETIME" if dialect == "sqlite" else "TIMESTAMP WITH TIME ZONE"
    additions = {
        "username": "VARCHAR(32) NULL",
        "terms_accepted_at": f"{datetime_type} NULL",
        "terms_version": "VARCHAR(40) NULL",
        "privacy_acknowledged_at": f"{datetime_type} NULL",
        "privacy_version": "VARCHAR(40) NULL",
    }
    with engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {sql_type}"))

        # Backfill stable, unique usernames for accounts created before this field existed.
        rows = connection.execute(text("SELECT id, email, username FROM users ORDER BY created_at, id")).mappings().all()
        used: set[str] = set()
        for row in rows:
            current = (row["username"] or "").strip().lower()
            if not current:
                base = (row["email"].split("@", 1)[0] or "planner").lower()
                base = re.sub(r"[^a-z0-9_.-]", "", base).strip("._-") or "planner"
                base = base[:24]
                current = base
                suffix = 2
                while current in used:
                    current = f"{base[:27]}{suffix}"
                    suffix += 1
                connection.execute(
                    text("UPDATE users SET username = :username WHERE id = :id"),
                    {"username": current, "id": row["id"]},
                )
            if current in used:
                base = current[:27]
                suffix = 2
                candidate = f"{base}{suffix}"
                while candidate in used:
                    suffix += 1
                    candidate = f"{base}{suffix}"
                current = candidate
                connection.execute(
                    text("UPDATE users SET username = :username WHERE id = :id"),
                    {"username": current, "id": row["id"]},
                )
            used.add(current)

        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users(username)"))
        if dialect == "postgresql":
            connection.execute(text("ALTER TABLE users ALTER COLUMN username SET NOT NULL"))
