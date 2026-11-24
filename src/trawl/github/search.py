import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from trawl.github.client import GithubClient, GithubUnavailable

log = logging.getLogger("trawl")

Count = Callable[[int, int], Awaitable[int]]


class SearchSettings(BaseModel):
    """GitHub search returns at most `result_cap` hits, so wide queries are cut into slices."""

    model_config = ConfigDict(extra="allow")

    page_size: int = 100
    result_cap: int = 1000
    size_ceiling: int = 400_000
    outage_rounds: int = 5
    outage_pause: int = 60
    date_qualifier: str = "pushed:{lo}..{hi}"
    size_qualifier: str = "size:{lo}..{hi}"
    stamp_format: str = "%Y-%m-%dT%H:%M:%SZ"


DEFAULTS = SearchSettings()


async def split_range(
    lo: int, hi: int, count: Count, cap: int = DEFAULTS.result_cap
) -> list[tuple[int, int]]:
    """Halve a numeric range until every part returns at most `cap` results."""
    if lo >= hi or await count(lo, hi) <= cap:
        return [(lo, hi)]
    middle = (lo + hi) // 2
    left, right = await asyncio.gather(
        split_range(lo, middle, count, cap), split_range(middle + 1, hi, count, cap)
    )
    return left + right


def stamp(seconds: int, fmt: str = DEFAULTS.stamp_format) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime(fmt)


async def total(github: GithubClient, path: str, query: str, kind: str) -> int:
    data = await github.json(path, {"q": query, "per_page": 1}, search=kind)
    return data["total_count"] if data else 0


async def slices(
    github: GithubClient,
    path: str,
    kind: str,
    query: str,
    qualifier: str,
    lo: int,
    hi: int,
    label: Callable[[int], str] = str,
    settings: SearchSettings = DEFAULTS,
) -> list[str]:
    """
    The query plus a qualifier template such as `pushed:{lo}..{hi}`, split so each
    slice fits under the result cap. `label` turns a number into the text the
    qualifier needs (a timestamp for dates, the number itself for sizes).
    """

    def text(a: int, b: int) -> str:
        return f"{query} {qualifier.format(lo=label(a), hi=label(b))}"

    async def count(a: int, b: int) -> int:
        return await total(github, path, text(a, b), kind)

    ranges = await split_range(lo, hi, count, settings.result_cap)
    return [text(a, b) for a, b in ranges]


async def repository_slices(
    github: GithubClient,
    query: str,
    since: datetime,
    until: datetime,
    settings: SearchSettings = DEFAULTS,
) -> list[str]:
    return await slices(
        github,
        "/search/repositories",
        "repositories",
        query,
        settings.date_qualifier,
        int(since.timestamp()),
        int(until.timestamp()),
        lambda seconds: stamp(seconds, settings.stamp_format),
        settings,
    )


async def code_slices(
    github: GithubClient, query: str, settings: SearchSettings = DEFAULTS
) -> list[str]:
    if await total(github, "/search/code", query, "code") <= settings.result_cap:
        return [query]
    return await slices(
        github,
        "/search/code",
        "code",
        query,
        settings.size_qualifier,
        0,
        settings.size_ceiling,
        settings=settings,
    )


async def pages(
    github: GithubClient,
    path: str,
    query: str,
    kind: str,
    start: int = 1,
    settings: SearchSettings = DEFAULTS,
) -> AsyncIterator[tuple[int, list[dict[str, Any]]]]:
    for page in range(start, settings.result_cap // settings.page_size + 1):
        data = await github.json(
            path,
            {"q": query, "per_page": settings.page_size, "page": page},
            search=kind,
        )
        items = data["items"] if data else []
        yield page, items
        if len(items) < settings.page_size:
            return


async def hits(
    github: GithubClient,
    path: str,
    query: str,
    kind: str,
    settings: SearchSettings = DEFAULTS,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    async for _, batch in pages(github, path, query, kind, settings=settings):
        items.extend(batch)
    return items


async def patient(
    query: str,
    run: Callable[[str], Awaitable[None]],
    settings: SearchSettings = DEFAULTS,
) -> None:
    """Run `run(query)` and wait out GitHub outages instead of failing."""
    for _ in range(settings.outage_rounds):
        try:
            await run(query)
            return
        except GithubUnavailable:
            log.warning(
                "github unreachable, retrying in %ss: %s", settings.outage_pause, query
            )
            await asyncio.sleep(settings.outage_pause)
    log.error("gave up after %s rounds: %s", settings.outage_rounds, query)
