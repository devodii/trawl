from trawl.stellar.strkey import (
    ACCOUNT,
    ACCOUNT_VERSION,
    find_contract_ids,
    find_strkeys,
    is_contract_id,
)

CONTRACT = "CDL74RF5BLYR2YBLCCI7F5FB6TPSCLKEJUBSD2RSVWZ4YHF3VMFAIGWA"
ACCOUNT_ID = "GAAZI4TCR3TY5OJHCTJC2A4QSY6CJWJH5IAJTGKIN2ER7LBNVKOCCWN7"


def test_only_checksummed_contract_ids_are_found_with_their_line():
    text = f"first\nid: {CONTRACT}\nfake: C{'A' * 55}\n"

    assert find_contract_ids(text) == [(CONTRACT, 2)]
    assert is_contract_id(CONTRACT) and not is_contract_id("C" + "A" * 55)


def test_the_pattern_and_version_choose_which_kind_of_key_is_read():
    found = find_strkeys(f"{ACCOUNT_ID} {CONTRACT}", ACCOUNT, ACCOUNT_VERSION)

    assert found == [(ACCOUNT_ID, 1)]
