from datetime import UTC, datetime

from trawl.github import search
from trawl.github.client import GithubUnavailable
from trawl.github.search import SearchSettings, patient, split_range, stamp


async def test_a_range_is_halved_until_every_part_fits_the_cap():
    async def count(lo, hi):
        return hi - lo + 1

    parts = await split_range(0, 99, count, cap=30)

    assert parts[0][0] == 0 and parts[-1][1] == 99
    assert all(count_part(lo, hi) <= 30 for lo, hi in parts)
    assert sum(count_part(lo, hi) for lo, hi in parts) == 100


def count_part(lo, hi):
    return hi - lo + 1


async def test_a_range_that_already_fits_is_left_whole():
    async def count(lo, hi):
        return 5

    assert await split_range(0, 99, count) == [(0, 99)]


def test_stamps_and_qualifier_templates_are_replaceable():
    moment = int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).timestamp())
    settings = SearchSettings(
        date_qualifier="created:{lo}..{hi}", stamp_format="%Y-%m-%d"
    )

    assert stamp(moment) == "2026-01-02T03:04:05Z"
    assert stamp(moment, settings.stamp_format) == "2026-01-02"
    assert settings.date_qualifier.format(lo="a", hi="b") == "created:a..b"


async def test_every_slice_fits_the_cap_and_none_is_lost():
    matches = list(range(0, 10_000, 7))

    async def count(lo: int, hi: int) -> int:
        return sum(1 for value in matches if lo <= value <= hi)

    ranges = await split_range(0, 10_000, count, cap=100)
    counts = [await count(lo, hi) for lo, hi in ranges]

    assert max(counts) <= 100
    assert sum(counts) == len(matches)
    assert all(a[1] + 1 == b[0] for a, b in zip(ranges, ranges[1:], strict=False))


async def test_a_single_value_is_never_split_even_over_the_cap():
    async def count(lo: int, hi: int) -> int:
        return 5_000

    assert await split_range(7, 7, count, cap=10) == [(7, 7)]


async def test_a_query_survives_a_connection_outage(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(search.asyncio, "sleep", instant)
    runs: list[str] = []

    async def flaky(query: str) -> None:
        runs.append(query)
        if len(runs) < 3:
            raise GithubUnavailable(query)

    await patient("q", flaky)

    assert runs == ["q", "q", "q"]


async def test_a_query_that_never_recovers_is_skipped_not_fatal(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(search.asyncio, "sleep", instant)
    runs: list[str] = []

    async def down(query: str) -> None:
        runs.append(query)
        raise GithubUnavailable(query)

    await patient("q", down, SearchSettings(outage_rounds=2))

    assert runs == ["q", "q"]
