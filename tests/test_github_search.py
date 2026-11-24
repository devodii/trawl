from datetime import UTC, datetime

from trawl.github.search import SearchSettings, split_range, stamp


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
