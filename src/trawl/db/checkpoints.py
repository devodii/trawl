from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from trawl.db.sql import load_sql


class Checkpoint(BaseModel):
    status: str
    cursor: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0

    @property
    def done(self) -> bool:
        return self.status == "done"


async def get(pool: asyncpg.Pool, job: str, key: str) -> Checkpoint | None:
    row = await pool.fetchrow(load_sql("checkpoint_get"), job, key)
    return Checkpoint(**dict(row)) if row else None


async def put(
    pool: asyncpg.Pool,
    job: str,
    key: str,
    status: str,
    cursor: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    await pool.execute(
        load_sql("checkpoint_put"), job, key, status, cursor or {}, error
    )
