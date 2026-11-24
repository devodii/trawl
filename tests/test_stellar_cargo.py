from trawl.stellar.cargo import CargoSettings, parse_cargo, parse_lock

MANIFEST = """
[package]
name = "token"
version = "0.1.0"

[lib]
crate-type = ["cdylib"]

[dependencies]
soroban-sdk = "22.0.7"
"""


def test_a_cdylib_that_depends_on_the_sdk_is_a_contract():
    info = parse_cargo("contracts/token/Cargo.toml", MANIFEST)

    assert info and info.is_contract
    assert info.soroban_sdk_requirement == "22.0.7"


def test_the_sdk_name_is_a_regex_setting():
    other = MANIFEST.replace("soroban-sdk", "my-sdk")

    assert not parse_cargo("a/Cargo.toml", other).is_contract
    assert parse_cargo(
        "a/Cargo.toml", other, settings=CargoSettings(sdk=r"my-.*")
    ).is_contract


def test_the_lock_keeps_only_packages_the_filter_matches():
    lock = '[[package]]\nname = "soroban-sdk"\nversion = "22.0.7"\n[[package]]\nname = "serde"\nversion = "1.0.0"\n'

    assert parse_lock(lock) == {"soroban-sdk": "22.0.7"}
    assert parse_lock(lock, CargoSettings(interesting=r"^serde$")) == {"serde": "1.0.0"}
