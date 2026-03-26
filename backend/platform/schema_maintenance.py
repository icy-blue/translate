from __future__ import annotations

from datetime import datetime

from sqlalchemy import inspect, text

from .config import engine

LOCAL_TIMEZONE_OFFSET = datetime.now().astimezone().strftime("%z")


def _build_postgres_fixed_offset_timezone(offset: str) -> str:
    if not offset:
        return "+00:00"
    sign = "-" if offset.startswith("+") else "+"
    return f"{sign}{offset[1:3]}:{offset[3:]}"


LOCAL_TIMEZONE_SQL = _build_postgres_fixed_offset_timezone(LOCAL_TIMEZONE_OFFSET)


def assert_message_schema_consistent():
    required_columns = {"message_kind", "section_category", "visible_to_user", "client_payload_json"}
    with engine.begin() as connection:
        inspector = inspect(connection)
        existing_columns = {column["name"] for column in inspector.get_columns("message")}
    missing_columns = sorted(required_columns - existing_columns)
    if missing_columns:
        missing_text = ", ".join(missing_columns)
        raise RuntimeError(
            "message table schema is inconsistent. Missing columns: "
            f"{missing_text}. Please run `python scripts/maintain_message_kind_schema.py --write` before starting the app."
        )


def ensure_asset_columns():
    dialect = engine.dialect.name
    binary_type = "BYTEA" if dialect == "postgresql" else "BLOB"
    required_columns = {
        "paperfigure": {"image_mime_type": "VARCHAR", "image_data": binary_type},
        "papertable": {"image_mime_type": "VARCHAR", "image_data": binary_type},
    }
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table_name, columns in required_columns.items():
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_type in columns.items():
                if column_name not in existing_columns:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
            if "image_url" in existing_columns:
                connection.execute(text(f"ALTER TABLE {table_name} DROP COLUMN image_url"))


def ensure_timestamp_timezone_columns():
    if engine.dialect.name != "postgresql":
        return
    target_columns = {
        "paperfigure": ["created_at"],
        "papertable": ["created_at"],
        "papertag": ["created_at"],
        "papersemanticscholarresult": ["created_at", "updated_at"],
    }
    with engine.begin() as connection:
        for table_name, column_names in target_columns.items():
            for column_name in column_names:
                data_type = connection.execute(
                    text(
                        """
                        SELECT data_type
                        FROM information_schema.columns
                        WHERE table_name = :table_name AND column_name = :column_name
                        """
                    ),
                    {"table_name": table_name, "column_name": column_name},
                ).scalar()
                if data_type is None or data_type == "timestamp with time zone":
                    continue
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} "
                        f"ALTER COLUMN {column_name} TYPE TIMESTAMPTZ "
                        f"USING {column_name} AT TIME ZONE '{LOCAL_TIMEZONE_SQL}'"
                    )
                )
