import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
from pydantic import BaseModel, ConfigDict

from trawl.github.client import GithubUnavailable

log = logging.getLogger("trawl")

NETWORK_ERRORS = (GithubUnavailable, httpx.TransportError, httpx.TimeoutException)


class Connectivity(BaseModel):
    """How to decide the network is back: GET `probe` and accept a status below `ok_below`."""

    model_config = ConfigDict(extra="allow")

    probe: str = "https://api.github.com/rate_limit"
    ok_below: int = 500
    timeout: float = 8
    first_pause: float = 5
    max_pause: float = 120


DEFAULT = Connectivity()


async def online(http: httpx.AsyncClient, settings: Connectivity = DEFAULT) -> bool:
    try:
        response = await http.get(settings.probe, timeout=settings.timeout)
    except NETWORK_ERRORS:
        return False
    return response.status_code < settings.ok_below


async def until_online(
    http: httpx.AsyncClient, settings: Connectivity = DEFAULT
) -> None:
    pause = settings.first_pause
    while not await online(http, settings):
        log.warning("offline, checking again in %ss", pause)
        await asyncio.sleep(pause)
        pause = min(pause * 2, settings.max_pause)


async def resilient[T](
    http: httpx.AsyncClient,
    call: Callable[[], Awaitable[T]],
    settings: Connectivity = DEFAULT,
    errors: tuple[type[BaseException], ...] = NETWORK_ERRORS,
) -> T:
    """Run `call`, and on a network error wait until the network is back, then run it again."""
    while True:
        try:
            return await call()
        except errors as error:
            log.warning(
                "network trouble (%s: %s), waiting to resume",
                type(error).__name__,
                str(error)[:120],
            )
            await until_online(http, settings)
