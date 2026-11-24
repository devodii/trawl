import json

import asyncpg


async def init_connection(connection: asyncpg.Connection) -> None:
    await connection.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def open_pool(url: str, size: int = 10) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        url, min_size=1, max_size=size, init=init_connection
    )
