from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlmodel import SQLModel

from ..platform.database import engine
from ..platform.models import register_sqlmodel_tables
from ..platform.schema_maintenance import (
    assert_message_schema_consistent,
    ensure_asset_columns,
    ensure_timestamp_timezone_columns,
)
from ..platform.task_runtime import recover_pending_tasks, start_task_workers, stop_task_workers


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    register_sqlmodel_tables()
    SQLModel.metadata.create_all(engine)
    ensure_asset_columns()
    assert_message_schema_consistent()
    ensure_timestamp_timezone_columns()
    recover_pending_tasks()
    start_task_workers()
    try:
        yield
    finally:
        await stop_task_workers()
