import pytest

from trawl.schema.guard import destructive_statements


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE IF EXISTS wasm_repos CASCADE;",
        "ALTER TABLE repos DROP COLUMN stars;",
        "ALTER TABLE repos ALTER COLUMN stars TYPE bigint;",
        "alter table repos drop column stars;",
        "TRUNCATE contracts;",
        "DELETE FROM projects;",
    ],
)
def test_the_guard_flags_statements_that_can_lose_data(sql):
    assert destructive_statements(sql)


def test_the_guard_lets_additive_and_data_safe_changes_through():
    sql = """
    CREATE TABLE notes (id text PRIMARY KEY);
    ALTER TABLE repos ADD COLUMN notes text;
    CREATE INDEX notes_id ON notes (id);
    DROP INDEX IF EXISTS contracts_wasm;
    DROP VIEW IF EXISTS attribution;
    DROP FUNCTION IF EXISTS method_rank(text);
    ALTER TABLE repos DROP CONSTRAINT repos_pkey;
    -- DROP TABLE contracts;
    """

    assert destructive_statements(sql) == []
