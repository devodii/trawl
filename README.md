# trawl

Shared toolkit for scrapers and indexers. A solid base for the parts every project rebuilds (GitHub access, schema tooling, checkpoints, git and Soroban readers), with every behaviour that differs between projects exposed as a setting, a regex or a callback.

Used by `hackproof`, `soroban-wrapped` and `state-of-soroban`.

## Install

```toml
[project]
dependencies = ["trawl @ git+https://github.com/devodii/trawl@v0.1.0"]

[tool.uv.sources]
trawl = { path = "../trawl", editable = true }   # only while developing
```

Extras for the optional parts: `trawl[parse]` (tree-sitter), `trawl[stellar]` (Stellar SDK, DuckDB), `trawl[sql]` (sqlfluff plugin).

## How it is meant to be used

- **Settings are pydantic models with `extra="allow"`.** Replace any field, or add your own and read it back from `model_extra`.
- **Patterns are regex strings.** Retry rules, repo URL shapes, attribute names, meta keys, noreply emails, schema layers and the destructive-change guard are all regexes you can swap.
- **Behaviour hooks are plain callables** (`Hooks.on_response`, `on_rate_limit`, `on_retry`).
- **Nothing reads global state or environment variables.** You pass the pool, the client and the settings in.

## Modules

| Module | What it gives you |
|---|---|
| `trawl.db` | `open_pool` (asyncpg with a jsonb codec), `load_sql` and `sql_loader(dir)` for `.sql` files, `checkpoints` (get and put, with the table in `db/ddl`) |
| `trawl.schema` | `pgschema` wrapper (plan, apply, verify through Docker), `Target` with replaceable `Layout` and guard rules, `Connection` parsing, the destructive-change `guard`, manifest builder ordered by dependency, a sqlfluff plugin |
| `trawl.github` | `GithubClient` (REST, GraphQL, raw; comma separated tokens, live `add_tokens`, per-token budgets, search throttles, regex retry policy), search slicing past the 1,000 result cap, repo reference parsing, offline waiting and `resilient` |
| `trawl.work` | `Stage` model, `Registry`, dependency ordering, regex based `select` for `only`, `skip` and `start` |
| `trawl.git` | blobless sparse clone, tracked files, commit log and patch parsing, with replaceable remote, noreply pattern and timeouts |
| `trawl.stellar` | Cargo manifest and lock reader, tree-sitter Rust reader, strkey validation, Soroban RPC client, wasm and contract spec reader, ledger lake day reader |

## Schema in a project

```python
from pathlib import Path
from trawl.schema import pgschema
from trawl.schema.connection import Connection

target = pgschema.Target(name="app", db_dir=Path("db"))
pgschema.apply(Connection.from_url(url), target=target)
pgschema.verify(Connection.from_url(url), target)
```

`db/schema/tables/*.sql` hold the tables, `db/schema/indexes/*.sql` the indexes, and the manifest is generated.

## CI

`.github/workflows/python.yml` is a reusable workflow (ruff, format check, sqlfluff, schema apply and verify on an empty Postgres, tests, build). A project's CI is a few lines:

```yaml
jobs:
  check:
    uses: devodii/trawl/.github/workflows/python.yml@v0.1.0
    with:
      sql-paths: db/schema src/app/sql
      schema-apply: uv run app schema apply
      schema-verify: uv run app schema verify
```

Pushing a tag `vX.Y.Z` publishes a GitHub release with the built package.

## Not here yet

Lifted from the three projects next: the autopilot and readiness signals, the Actions shard runner, COPY based bulk upserts, an ETag cache and a shared token store, the snapshot cache, a `trawl new` scaffold. The plan is in `state-of-soroban/out/trawl-plan.html`.
