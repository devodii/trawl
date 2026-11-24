from trawl.db.sql import load_sql, sql_loader


def test_a_query_is_read_from_the_given_folder(tmp_path):
    (tmp_path / "one.sql").write_text("SELECT 1")

    assert load_sql("one", tmp_path) == "SELECT 1"
    assert sql_loader(tmp_path)("one") == "SELECT 1"
