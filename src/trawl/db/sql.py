from collections.abc import Callable
from functools import cache
from pathlib import Path

PACKAGE_SQL = Path(__file__).with_name("sql")


@cache
def read_sql(directory: Path, name: str) -> str:
    return (directory / f"{name}.sql").read_text()


def load_sql(name: str, directory: Path = PACKAGE_SQL) -> str:
    return read_sql(directory, name)


def sql_loader(directory: Path) -> Callable[[str], str]:
    def load(name: str) -> str:
        return read_sql(directory, name)

    return load
