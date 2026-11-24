import re

from trawl.github.refs import normalize_repo


def test_urls_references_and_bare_names_become_owner_and_repo():
    assert normalize_repo("https://github.com/stellar/js-stellar-sdk.git") == (
        "stellar/js-stellar-sdk"
    )
    assert normalize_repo("github:stellar/rs-soroban-sdk/tree/main") == (
        "stellar/rs-soroban-sdk"
    )
    assert normalize_repo(" stellar/stellar-cli ") == "stellar/stellar-cli"
    assert normalize_repo("not a repo") is None


def test_the_pattern_can_be_replaced_to_read_another_host():
    gitlab = re.compile(r"^https://gitlab\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)$")

    assert normalize_repo("https://gitlab.com/a/b", gitlab) == "a/b"
