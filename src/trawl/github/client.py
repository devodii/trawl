import asyncio
import re
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

TOKEN_SEPARATOR = re.compile(r"[,\s]+")


class GithubUnavailable(Exception):
    pass


def parse_tokens(value: str | list[str] | None) -> list[str]:
    """Tokens from `a,b c` or a list, in any mix of commas, spaces and new lines."""
    if value is None:
        return []
    parts = TOKEN_SEPARATOR.split(value) if isinstance(value, str) else value
    return [part.strip() for part in parts if part and part.strip()]


class Policy(BaseModel):
    """
    Everything that decides how the client behaves. All fields can be replaced, and
    unknown fields are kept so a project can carry its own settings alongside.
    """

    model_config = ConfigDict(extra="allow")

    api: str = "https://api.github.com"
    raw: str = "https://raw.githubusercontent.com"
    accept: str = "application/vnd.github+json"
    headers: dict[str, str] = Field(default_factory=dict)
    search_interval: dict[str, float] = Field(
        default_factory=lambda: {"code": 6.5, "repositories": 2.2}
    )
    default_search_interval: float = 3.0
    retryable: set[int] = Field(
        default_factory=lambda: {403, 408, 429, 500, 502, 503, 504}
    )
    retry_pattern: str | None = None
    absent: set[int] = Field(default_factory=lambda: {404, 409, 451})
    attempts: int = 6
    min_budget: int = 20
    default_budget: int = 5000
    graphql_ignored_errors: str = r"^NOT_FOUND$"


class Token(BaseModel):
    value: str | None
    label: str = ""
    budget: dict[str, int] = Field(default_factory=dict)
    blocked_until: float = 0.0
    next_search_at: dict[str, float] = Field(default_factory=dict)


class Hooks(BaseModel):
    """Optional callbacks. Each receives plain values and its return value is ignored."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    on_response: Callable[[Token, httpx.Response], None] | None = None
    on_rate_limit: Callable[[Token, float], None] | None = None
    on_retry: Callable[[int, str, str], None] | None = None


class GithubClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        tokens: str | list[str] | None = None,
        policy: Policy | None = None,
        hooks: Hooks | None = None,
    ) -> None:
        self._http = http
        self.policy = policy or Policy()
        self.hooks = hooks or Hooks()
        self._tokens = [self._token(v) for v in parse_tokens(tokens)] or [
            Token(value=None, label="anonymous")
        ]
        self._lock = asyncio.Lock()
        self._raw_resume_at = 0.0
        self._retry = (
            re.compile(self.policy.retry_pattern) if self.policy.retry_pattern else None
        )

    @staticmethod
    def _token(value: str) -> Token:
        return Token(value=value, label=f"…{value[-4:]}")

    def add_tokens(self, values: str | list[str]) -> int:
        known = {t.value for t in self._tokens}
        fresh = [v for v in parse_tokens(values) if v not in known]
        if fresh and all(t.value is None for t in self._tokens):
            self._tokens = []
        self._tokens.extend(self._token(v) for v in fresh)
        return len(fresh)

    def _budget(self, token: Token, resource: str) -> int:
        return token.budget.get(resource, self.policy.default_budget)

    def _interval(self, kind: str) -> float:
        return self.policy.search_interval.get(
            kind, self.policy.default_search_interval
        )

    async def _acquire(self, search: str | None, resource: str) -> Token:
        while True:
            async with self._lock:
                now = time.monotonic()
                usable = [
                    t
                    for t in self._tokens
                    if t.blocked_until <= now
                    and self._budget(t, resource) > self.policy.min_budget
                ]
                if usable and search is None:
                    return max(usable, key=lambda t: self._budget(t, resource))
                if usable:
                    token = min(usable, key=lambda t: t.next_search_at.get(search, 0.0))
                    start = max(token.next_search_at.get(search, 0.0), now)
                    token.next_search_at[search] = start + self._interval(search)
                    wait = start - now
                else:
                    token = None
                    wait = max(min(t.blocked_until for t in self._tokens) - now, 1)
            await asyncio.sleep(wait)
            if token is not None:
                return token

    def _record(self, token: Token, response: httpx.Response) -> None:
        remaining = response.headers.get("x-ratelimit-remaining")
        resource = response.headers.get("x-ratelimit-resource")
        if remaining is not None and resource:
            token.budget[resource] = int(remaining)
        limited = response.status_code in (403, 429)
        if limited and remaining == "0":
            reset = int(response.headers.get("x-ratelimit-reset", time.time() + 60))
            token.blocked_until = time.monotonic() + max(reset - time.time(), 1)
        elif limited and "retry-after" in response.headers:
            token.blocked_until = time.monotonic() + int(
                response.headers["retry-after"]
            )
        if self.hooks.on_response:
            self.hooks.on_response(token, response)
        if (
            limited
            and token.blocked_until > time.monotonic()
            and self.hooks.on_rate_limit
        ):
            self.hooks.on_rate_limit(token, token.blocked_until)

    def _should_retry(self, response: httpx.Response) -> bool:
        if response.status_code in self.policy.retryable:
            return True
        return bool(
            self._retry
            and self._retry.search(f"{response.status_code} {response.text[:300]}")
        )

    def _note_retry(self, attempt: int, url: str, reason: str) -> None:
        if self.hooks.on_retry:
            self.hooks.on_retry(attempt, url, reason)

    async def _send(
        self,
        method: str,
        url: str,
        *,
        search: str | None = None,
        resource: str = "core",
        **options: Any,
    ) -> httpx.Response:
        for attempt in range(self.policy.attempts):
            token = await self._acquire(search, resource)
            headers = {"Accept": self.policy.accept, **self.policy.headers}
            if token.value:
                headers["Authorization"] = f"Bearer {token.value}"
            try:
                response = await self._http.request(
                    method, url, headers=headers, **options
                )
            except httpx.TransportError as error:
                self._note_retry(attempt, url, type(error).__name__)
                await asyncio.sleep(2**attempt / 2)
                continue
            self._record(token, response)
            if self._should_retry(response):
                self._note_retry(attempt, url, str(response.status_code))
                await asyncio.sleep(2**attempt / 4)
                continue
            return response
        raise GithubUnavailable(url)

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        search: str | None = None,
    ) -> httpx.Response | None:
        response = await self._send(
            "GET", f"{self.policy.api}{path}", search=search, params=params
        )
        if response.status_code in self.policy.absent:
            return None
        response.raise_for_status()
        return response

    async def json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        search: str | None = None,
    ) -> Any | None:
        response = await self.get(path, params, search=search)
        if response is None or response.status_code == 204:
            return None
        return response.json()

    async def graphql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        ignored = re.compile(self.policy.graphql_ignored_errors)
        body = {"query": query, **({"variables": variables} if variables else {})}
        for attempt in range(self.policy.attempts):
            response = await self._send(
                "POST",
                f"{self.policy.api}/graphql",
                resource="graphql",
                json=body,
            )
            try:
                payload = response.json()
            except ValueError:
                await asyncio.sleep(2**attempt / 2)
                continue
            errors = [
                e
                for e in payload.get("errors", [])
                if not ignored.search(str(e.get("type", "")))
            ]
            if errors or "data" not in payload:
                self._note_retry(attempt, "graphql", str(errors[:1])[:120])
                await asyncio.sleep(2**attempt / 2)
                continue
            return payload["data"]
        raise GithubUnavailable("graphql")

    async def raw(
        self, full_name: str, ref: str, path: str, limit: int = 512_000
    ) -> bytes | None:
        url = f"{self.policy.raw}/{full_name}/{ref}/{path}"
        for attempt in range(self.policy.attempts):
            await asyncio.sleep(max(self._raw_resume_at - time.monotonic(), 0))
            try:
                response = await self._http.get(url)
            except httpx.TransportError:
                await asyncio.sleep(2**attempt / 2)
                continue
            if response.status_code in self.policy.retryable:
                pause = float(response.headers.get("retry-after", 2 ** (attempt + 1)))
                self._raw_resume_at = max(self._raw_resume_at, time.monotonic() + pause)
                continue
            if response.status_code != 200 or len(response.content) > limit:
                return None
            return response.content
        raise GithubUnavailable(url)
