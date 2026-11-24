from pathlib import Path

from trawl.stellar.rust import parse_rust

FIXTURE = Path(__file__).parent / "fixtures" / "token.rs"


def parsed():
    return parse_rust("contracts/token/src/lib.rs", FIXTURE.read_bytes())


def test_functions_carry_their_contract_and_test_context():
    functions = {f.name: f for f in parsed().functions}

    assert functions["initialize"].is_contract_fn
    assert functions["initialize"].impl_type == "Token"
    assert functions["setup"].is_test and not functions["setup"].is_contract_fn
    assert functions["works"].is_test


def test_parameter_attributes_and_comments_are_understood():
    result = parsed()
    set_fee = next(f for f in result.functions if f.name == "set_fee")

    assert set_fee.params[1].attributes == ["auth"]
    assert "a comment about" not in result.clean
    assert "require_auth" in set_fee.calls


def test_types_expose_their_members_and_flags():
    types = {t.name: t for t in parsed().types}

    assert types["DataKey"].is_contracttype
    assert [m.name for m in types["DataKey"].members] == ["Admin", "Balance"]
    assert types["Error"].is_contracterror
    assert parsed().macros == ["def:guard"]


def test_the_same_shape_hashes_the_same_whatever_the_names():
    first = b"fn a(env: Env) { let x = 1; env.storage().instance().get(&x) }"
    second = (
        b"fn zzz(env: Env) { let hello = 99; env.storage().instance().get(&hello) }"
    )

    one = parse_rust("a.rs", first).functions[0]
    two = parse_rust("b.rs", second).functions[0]

    assert one.shape_hash == two.shape_hash
    assert one.body_hash != two.body_hash


def test_the_attribute_regexes_are_settings():
    from trawl.stellar.rust import RustSettings

    source = b"#[my_contract]\nimpl Token { pub fn mint(env: Env) {} }\n"

    default = parse_rust("src/lib.rs", source)
    custom = parse_rust(
        "src/lib.rs", source, RustSettings(contract_impl=r"^my_contract")
    )

    assert not default.functions[0].is_contract_fn
    assert custom.functions[0].is_contract_fn
