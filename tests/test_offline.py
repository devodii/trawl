import httpx

from trawl.github.offline import Connectivity, online, resilient


def http(status: int | None) -> httpx.AsyncClient:
    def handler(request):
        if status is None:
            raise httpx.ConnectError("down")
        return httpx.Response(status)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_online_means_the_probe_answers_below_the_limit():
    assert await online(http(200))
    assert not await online(http(503))
    assert not await online(http(None))
    assert await online(http(404), Connectivity(ok_below=500))


async def test_a_call_that_fails_on_the_network_runs_again_once_it_is_back():
    attempts = []

    async def call():
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("down")
        return "done"

    settings = Connectivity(first_pause=0, max_pause=0)

    assert await resilient(http(200), call, settings) == "done"
    assert len(attempts) == 3
