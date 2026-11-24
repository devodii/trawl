import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, ConfigDict, Field

from trawl.schema.connection import Connection
from trawl.schema.guard import RULES, destructive_statements
from trawl.schema.manifest import (
    DEFAULT_LAYOUT,
    Layout,
    generate_manifest,
    manifest_text,
)

DEFAULT_IMAGE = "pgplex/pgschema:1.14.1"
MANIFEST = "/db/schema/manifest.sql"
LOCK_TIMEOUT = "30s"


class Target(BaseModel):
    """One database and the schema and migration files that describe it."""

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str
    db_dir: Path
    image: str = DEFAULT_IMAGE
    layout: Layout = DEFAULT_LAYOUT
    rules: list[tuple[str, str]] = Field(default_factory=lambda: list(RULES))

    @property
    def schema_dir(self) -> Path:
        return self.db_dir / "schema"

    @property
    def migrations_dir(self) -> Path:
        return self.db_dir / "migrations"

    @property
    def root(self) -> Path:
        return self.db_dir.parent


def docker_command(
    command: str,
    *args: str,
    connection: Connection,
    mounts: dict[Path, str],
    image: str,
) -> list[str]:
    network, host = connection.container_route()
    mount_flags = [
        flag
        for source, target in mounts.items()
        for flag in ("-v", f"{source}:{target}")
    ]
    return [
        "docker",
        "run",
        "--rm",
        "-i",
        *network,
        "-e",
        "PGPASSWORD",
        *mount_flags,
        image,
        command,
        "--host",
        host,
        "--port",
        str(connection.port),
        "--db",
        connection.database,
        "--user",
        connection.user,
        "--schema",
        "public",
        "--sslmode",
        connection.sslmode,
        *args,
    ]


def run_pgschema(
    command: str,
    *args: str,
    connection: Connection,
    target: Target,
    mounts: dict[Path, str] | None = None,
) -> int:
    cmd = docker_command(
        command,
        *args,
        connection=connection,
        mounts={target.db_dir: "/db", **(mounts or {})},
        image=target.image,
    )
    env = {**os.environ, "PGPASSWORD": connection.password}
    return subprocess.run(cmd, env=env, check=False).returncode


def format_schema(target: Target) -> None:
    """Fix what sqlfluff can, then lint so what is left shows its file and rule."""
    for action in ("fix", "lint"):
        subprocess.run(
            [sys.executable, "-m", "sqlfluff", action, str(target.schema_dir)],
            cwd=target.root,
            check=False,
        )


def next_migration_path(target: Target) -> Path:
    target.migrations_dir.mkdir(parents=True, exist_ok=True)
    numbers = [
        int(f.stem)
        for f in target.migrations_dir.glob("[0-9][0-9][0-9][0-9][0-9][0-9].sql")
    ]
    return target.migrations_dir / f"{max(numbers, default=0) + 1:06d}.sql"


def plan_sql(connection: Connection, out_dir: Path, target: Target) -> tuple[int, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o777)
    returncode = run_pgschema(
        "plan",
        "--file",
        MANIFEST,
        "--output-json",
        "/out/plan.json",
        "--output-sql",
        "/out/plan.sql",
        connection=connection,
        target=target,
        mounts={out_dir: "/out"},
    )
    sql_file = out_dir / "plan.sql"
    return returncode, sql_file.read_text() if sql_file.is_file() else ""


def plan(connection: Connection, *, format: bool = True, target: Target) -> int:
    if format:
        format_schema(target)
    generate_manifest(target.schema_dir, target.layout)
    path = next_migration_path(target)
    returncode = run_pgschema(
        "plan",
        "--file",
        MANIFEST,
        "--output-sql",
        f"/db/migrations/{path.name}",
        connection=connection,
        target=target,
    )
    if returncode != 0:
        return returncode
    if not path.is_file() or path.stat().st_size == 0:
        path.unlink(missing_ok=True)
        print("No changes detected.")
        return 0
    print(f"Wrote the migration plan to {path.relative_to(target.root)}")
    return 0


def apply(
    connection: Connection,
    *,
    allow_destructive: bool = False,
    out_dir: Path | None = None,
    target: Target,
) -> int:
    generate_manifest(target.schema_dir, target.layout)
    with TemporaryDirectory() as scratch:
        work_dir = out_dir or Path(scratch)
        work_dir.mkdir(parents=True, exist_ok=True)
        returncode, sql = plan_sql(connection, work_dir, target)
        if returncode != 0:
            return returncode
        if not sql.strip():
            print(f"No changes: {connection.label} already matches the schema files.")
            return 0
        print(f"Pending changes for {connection.label}:\n\n{sql}")
        findings = destructive_statements(sql, target.rules)
        if findings and not allow_destructive:
            print("Refusing to apply: the plan could lose data.", file=sys.stderr)
            for finding in findings:
                print(f"  {finding.reason}: {finding.statement}", file=sys.stderr)
            return 2
        return run_pgschema(
            "apply",
            "--plan",
            "/out/plan.json",
            "--auto-approve",
            "--lock-timeout",
            LOCK_TIMEOUT,
            connection=connection,
            target=target,
            mounts={work_dir: "/out"},
        )


def verify(connection: Connection, target: Target) -> int:
    manifest = target.schema_dir / "manifest.sql"
    if not manifest.is_file() or manifest.read_text() != manifest_text(
        target.schema_dir, target.layout
    ):
        print(
            "manifest.sql is out of date: run the schema plan command.",
            file=sys.stderr,
        )
        return 1
    with TemporaryDirectory() as scratch:
        returncode, sql = plan_sql(connection, Path(scratch), target)
    if returncode != 0:
        return returncode
    if sql.strip():
        print(
            f"{connection.label} differs from the schema files:\n\n{sql}",
            file=sys.stderr,
        )
        return 1
    print(f"In sync: {connection.label} matches the schema files.")
    return 0
