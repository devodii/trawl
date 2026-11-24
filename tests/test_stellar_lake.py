from datetime import date

import httpx

from trawl.stellar.lake import LakeSettings, list_day, split_base

LISTING = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Contents><Key>v1/date=2026-01-01/200.parquet</Key></Contents>
  <Contents><Key>v1/date=2026-01-01/100.parquet</Key></Contents>
  <Contents><Key>v1/date=2026-01-01/readme.txt</Key></Contents>
</ListBucketResult>"""


def http() -> httpx.AsyncClient:
    def handler(request):
        assert request.url.params["prefix"] == "v1/date=2026-01-01/"
        return httpx.Response(200, text=LISTING)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_a_base_url_splits_into_origin_and_prefix():
    assert split_base("https://bucket.s3.amazonaws.com/v1/stellar/") == (
        "https://bucket.s3.amazonaws.com",
        "v1/stellar",
    )


async def test_the_day_listing_returns_ledger_files_in_order():
    urls = await list_day(
        http(), "https://bucket.s3.amazonaws.com/v1", date(2026, 1, 1)
    )

    assert [u.rsplit("/", 1)[1] for u in urls] == ["100.parquet", "200.parquet"]


def test_the_topic_filter_and_file_pattern_are_settings():
    settings = LakeSettings(
        call_topic='[{"symbol":"transfer"}%', ledger_file=r"/(\d+)\.pq$"
    )

    assert settings.call_topic.startswith('[{"symbol":"transfer"')
    assert settings.model_dump()["chunk"] == 250
