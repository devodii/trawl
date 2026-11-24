from pathlib import Path

from trawl.schema import pgschema
from trawl.schema.connection import Connection

NEON = "postgresql://owner:p%40ss@ep-cool-1234.us-east-2.aws.neon.tech/app?sslmode=require&channel_binding=require"


def target(tmp_path: Path) -> pgschema.Target:
    return pgschema.Target(name="app", db_dir=tmp_path / "db")


def test_the_password_never_appears_in_the_docker_command(tmp_path):
    command = pgschema.docker_command(
        "plan",
        "--file",
        "/db/schema/manifest.sql",
        connection=Connection.from_url(NEON),
        mounts={tmp_path: "/db"},
        image="pgplex/pgschema:1.14.1",
    )

    assert not any("p@ss" in part or "p%40ss" in part for part in command)
    assert command[command.index("-e") + 1] == "PGPASSWORD"
    assert command[command.index("--sslmode") + 1] == "require"
    assert f"{tmp_path}:/db" in command


def test_migrations_are_numbered_one_after_another(tmp_path):
    here = target(tmp_path)
    assert pgschema.next_migration_path(here).name == "000001.sql"

    for name in ("000001.sql", "000002.sql", "notes.md"):
        (here.migrations_dir / name).write_text("x")

    assert pgschema.next_migration_path(here).name == "000003.sql"


def test_apply_runs_the_reviewed_plan_with_a_lock_timeout(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(
        pgschema,
        "run_pgschema",
        lambda command, *args, **kwargs: seen.append((command, args)) or 0,
    )
    monkeypatch.setattr(
        pgschema, "plan_sql", lambda *_: (0, "CREATE TABLE x (id text);")
    )
    monkeypatch.setattr(pgschema, "generate_manifest", lambda *_: None)

    code = pgschema.apply(
        Connection.from_url(NEON), out_dir=tmp_path, target=target(tmp_path)
    )

    assert code == 0
    command, args = seen[0]
    assert command == "apply"
    assert args[:2] == ("--plan", "/out/plan.json")
    assert args[args.index("--lock-timeout") + 1] == "30s"


def test_apply_refuses_a_plan_that_drops_data_and_never_runs_it(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(pgschema, "run_pgschema", lambda *a, **k: ran.append(a) or 0)
    monkeypatch.setattr(pgschema, "plan_sql", lambda *_: (0, "DROP TABLE contracts;"))
    monkeypatch.setattr(pgschema, "generate_manifest", lambda *_: None)
    here = target(tmp_path)

    refused = pgschema.apply(Connection.from_url(NEON), out_dir=tmp_path, target=here)
    allowed = pgschema.apply(
        Connection.from_url(NEON),
        allow_destructive=True,
        out_dir=tmp_path,
        target=here,
    )

    assert refused == 2
    assert len(ran) == 1
    assert allowed == 0
