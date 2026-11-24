import sqlfluff


def codes(sql: str) -> set[str]:
    found = sqlfluff.lint(sql, dialect="postgres", rules=["custom.create_type_upper"])
    return {violation["name"] for violation in found}


def test_create_type_names_must_be_upper_case():
    assert codes("CREATE TYPE status AS ENUM ('a');\n") == {"custom.create_type_upper"}
    assert codes("CREATE TYPE STATUS AS ENUM ('a');\n") == set()
