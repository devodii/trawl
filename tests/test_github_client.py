import httpx

from trawl.github.client import GithubClient, GithubUnavailable, Policy, parse_tokens


def test_tokens_split_on_commas_spaces_and_new_lines():
    assert parse_tokens("a, b\nc  d,,") == ["a", "b", "c", "d"]
    assert parse_tokens(["a", " b "]) == ["a", "b"]
    assert parse_tokens(None) == []


def client(handler, **policy) -> GithubClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GithubClient(http, "one,two", Policy(attempts=3, **policy))


def test_new_tokens_are_added_once_and_extra_policy_fields_are_kept():
    github = client(lambda request: httpx.Response(200), project="trawl")

    assert github.add_tokens("two, three") == 1
    assert github.policy.model_extra == {"project": "trawl"}


async def test_a_missing_repo_is_none_and_a_found_one_is_json():
    def handler(request):
        if request.url.path.endswith("gone"):
            return httpx.Response(404)
        return httpx.Response(200, json={"name": "kept"})

    github = client(handler)

    assert await github.json("/repos/a/gone") is None
    assert await github.json("/repos/a/kept") == {"name": "kept"}


async def test_a_retry_regex_can_turn_any_response_into_a_retry():
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) < 3:
            return httpx.Response(200, text="abuse detection triggered")
        return httpx.Response(200, json={"ok": True})

    github = client(handler, retry_pattern=r"abuse detection")

    assert await github.json("/x") == {"ok": True}
    assert len(seen) == 3


async def test_giving_up_raises_unavailable():
    github = client(lambda request: httpx.Response(502))

    try:
        await github.json("/x")
    except GithubUnavailable:
        return
    raise AssertionError("expected GithubUnavailable")
