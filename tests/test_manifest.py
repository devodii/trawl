from pathlib import Path

from trawl.schema.manifest import manifest_text, order_by_dependency


def write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def test_tables_load_after_the_tables_they_reference(tmp_path):
    root = write(
        tmp_path,
        {
            "a_child.sql": "CREATE TABLE a_child (p text REFERENCES z_parent (id));",
            "z_parent.sql": "CREATE TABLE z_parent (id text PRIMARY KEY);",
        },
    )

    assert [f.name for f in order_by_dependency("tables", root)] == [
        "z_parent.sql",
        "a_child.sql",
    ]


def test_views_load_after_the_views_they_select_from(tmp_path):
    root = write(
        tmp_path,
        {
            "account_projects.sql": "CREATE OR REPLACE VIEW account_projects AS SELECT 1 FROM attribution;",
            "attribution.sql": "CREATE OR REPLACE VIEW attribution AS SELECT 1 FROM links;",
        },
    )

    assert [f.name for f in order_by_dependency("views", root)] == [
        "attribution.sql",
        "account_projects.sql",
    ]


def test_the_manifest_lists_layers_in_load_order(tmp_path):
    write(
        tmp_path,
        {
            "tables/a.sql": "CREATE TABLE a (id text PRIMARY KEY);",
            "indexes/a_id.sql": "CREATE INDEX a_id ON a (id);",
        },
    )

    assert manifest_text(tmp_path) == "\\i tables/a.sql\n\\i indexes/a_id.sql\n"
