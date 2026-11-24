import httpx

from trawl.stellar.rpc import RpcClient, RpcSettings


def client(handler, urls=("https://a", "https://b")) -> RpcClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RpcClient(http, list(urls), RpcSettings(rate_limit_pause=0, backoff=0))


async def test_the_next_endpoint_is_tried_when_one_errors():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "a":
            return httpx.Response(200, json={"error": {"message": "down"}})
        return httpx.Response(200, json={"result": {"ok": True}})

    assert await client(handler).call("getHealth") == {"ok": True}
    assert seen == ["https://a", "https://b"]


async def test_entries_track_the_latest_ledger_and_expiry():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "result": {
                    "latestLedger": 99,
                    "entries": [{"key": "k", "xdr": "x", "liveUntilLedgerSeq": 120}],
                }
            },
        )

    rpc = client(handler)
    found = await rpc.entries(["k"])

    assert rpc.latest == 99 and found["k"].live_until == 120
