import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import duckdb
import httpx
from pydantic import BaseModel, ConfigDict

log = logging.getLogger("trawl")

NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


class LakeSettings(BaseModel):
    """
    How the public ledger archive is read. `call_topic` is a SQL LIKE pattern for the
    diagnostic events to keep and `ledger_file` a regex whose first group is the
    ledger number. Extra fields are kept for project use.
    """

    model_config = ConfigDict(extra="allow")

    chunk: int = 250
    call_topic: str = '[{"symbol":"fn_call"}%'
    ledger_file: str = r"/(\d+)\.parquet$"
    partition: str = "{prefix}/date={day}/"


DEFAULT = LakeSettings()


class DayResult(BaseModel):
    day: date
    ledgers: int
    calls: int
    seconds: float
    files: list[str]


def split_base(base: str) -> tuple[str, str]:
    parsed = urlparse(base)
    return f"{parsed.scheme}://{parsed.netloc}", parsed.path.strip("/")


async def list_day(
    http: httpx.AsyncClient,
    base: str,
    day: date,
    settings: LakeSettings = DEFAULT,
) -> list[str]:
    origin, prefix = split_base(base)
    token: str | None = None
    urls: list[tuple[int, str]] = []
    while True:
        params = {
            "list-type": "2",
            "prefix": settings.partition.format(prefix=prefix, day=day.isoformat()),
        }
        if token:
            params["continuation-token"] = token
        response = await http.get(origin, params=params, timeout=60)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        for item in root.findall("s3:Contents/s3:Key", NS):
            match = re.search(settings.ledger_file, item.text or "")
            if match:
                urls.append((int(match.group(1)), f"{origin}/{item.text}"))
        more = root.find("s3:NextContinuationToken", NS)
        if more is None or not more.text:
            break
        token = more.text
    return [url for _, url in sorted(urls)]


def connect(threads: int) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute("INSTALL httpfs; LOAD httpfs;")
    connection.execute(f"SET threads={threads}; SET http_retries=6;")
    connection.execute(
        """
        CREATE TABLE calls (
            day DATE, ledger BIGINT, tx VARCHAR, src VARCHAR, ok BOOLEAN,
            caller VARCHAR, callee VARCHAR, fn VARCHAR
        );
        CREATE TABLE tx_stats (day DATE, ledger BIGINT, ok BOOLEAN, ops INTEGER);
        """
    )
    return connection


def read_chunk(
    connection: duckdb.DuckDBPyConnection,
    files: list[str],
    settings: LakeSettings = DEFAULT,
) -> None:
    connection.execute(
        """
        INSERT INTO tx_stats
        SELECT CAST(closed_at AS DATE), sequence, t.successful, t.operation_count
        FROM (
            SELECT closed_at, sequence, unnest(transactions) AS t
            FROM read_parquet($files)
        )
        """,
        {"files": files},
    )
    connection.execute(
        f"""
        INSERT INTO calls
        SELECT day, ledger, tx, src, ok, e.contract_id,
               json_extract_string(e.topics, '$[1].bytes'),
               json_extract_string(e.topics, '$[2].symbol')
        FROM (
            SELECT CAST(closed_at AS DATE) AS day, sequence AS ledger,
                   t.transaction_hash AS tx, t.account AS src, t.successful AS ok,
                   unnest(t.diagnostic_events) AS e
            FROM (
                SELECT closed_at, sequence, unnest(transactions) AS t
                FROM read_parquet($files)
            )
        )
        WHERE e.topics LIKE '{settings.call_topic}'
        """,
        {"files": files},
    )


def write_outputs(
    connection: duckdb.DuckDBPyConnection, out: Path, day: date
) -> dict[str, str]:
    out.mkdir(parents=True, exist_ok=True)
    names = {
        "activity": f"activity-{day}.parquet",
        "functions": f"functions-{day}.parquet",
        "edges": f"edges-{day}.parquet",
        "network": f"network-{day}.parquet",
    }
    queries = {
        "activity": """
            SELECT day, callee, count(*) AS calls,
                   count(*) FILTER (WHERE ok) AS successful,
                   count(DISTINCT tx) AS txs, count(DISTINCT src) AS accounts,
                   count(*) FILTER (WHERE caller IS NULL) AS direct_calls
            FROM calls GROUP BY day, callee""",
        "functions": """
            SELECT day, callee, fn, count(*) AS calls
            FROM calls GROUP BY day, callee, fn""",
        "edges": """
            SELECT day, caller, callee, fn, count(*) AS calls,
                   count(*) FILTER (WHERE ok) AS successful
            FROM calls WHERE caller IS NOT NULL GROUP BY day, caller, callee, fn""",
        "network": """
            SELECT s.day, count(DISTINCT s.ledger) AS ledgers, count(*) AS transactions,
                   count(*) FILTER (WHERE NOT s.ok) AS failed_transactions,
                   sum(s.ops) AS operations,
                   (SELECT count(*) FROM calls c WHERE c.day = s.day) AS contract_calls,
                   (SELECT count(DISTINCT callee) FROM calls c WHERE c.day = s.day) AS contracts_called
            FROM tx_stats s GROUP BY s.day""",
    }
    for name, query in queries.items():
        connection.execute(f"COPY ({query}) TO '{out / names[name]}' (FORMAT PARQUET)")
    return {name: str(out / file) for name, file in names.items()}


async def scan_day(
    http: httpx.AsyncClient,
    base: str,
    day: date,
    out: Path,
    threads: int = 32,
    limit_ledgers: int | None = None,
    settings: LakeSettings = DEFAULT,
) -> DayResult:
    started = time.monotonic()
    urls = await list_day(http, base, day, settings)
    if limit_ledgers:
        urls = urls[:limit_ledgers]
    connection = connect(threads)
    for offset in range(0, len(urls), settings.chunk):
        read_chunk(connection, urls[offset : offset + settings.chunk], settings)
        done = min(offset + settings.chunk, len(urls))
        log.info(
            "%s: %s/%s ledgers, %.1f ledgers/s",
            day,
            done,
            len(urls),
            done / max(time.monotonic() - started, 0.001),
        )
    calls = connection.execute("SELECT count(*) FROM calls").fetchone()[0]
    files = write_outputs(connection, out, day)
    connection.close()
    return DayResult(
        day=day,
        ledgers=len(urls),
        calls=calls,
        seconds=round(time.monotonic() - started, 1),
        files=list(files.values()),
    )
