import re
from pathlib import Path

from pydantic import BaseModel, Field

NAME = r'"?([A-Za-z_][A-Za-z0-9_]*)"?'


class Layout(BaseModel):
    """
    How a schema directory is read. Every field is a plain value or a regex, so a
    project can add layers (for example `policies`) or change what counts as an
    object name or a reference without touching code.
    """

    layers: list[str] = Field(
        default_factory=lambda: [
            "types",
            "functions",
            "tables",
            "constraints",
            "indexes",
            "views",
            "triggers",
        ]
    )
    objects: dict[str, str] = Field(
        default_factory=lambda: {
            "tables": rf"CREATE TABLE(?: IF NOT EXISTS)? {NAME}",
            "views": rf"CREATE (?:OR REPLACE )?VIEW {NAME}",
        }
    )
    references: dict[str, str] = Field(
        default_factory=lambda: {
            "tables": rf"REFERENCES {NAME}",
            "views": rf"(?:FROM|JOIN)\s+{NAME}",
        }
    )
    flags: int = re.IGNORECASE


DEFAULT_LAYOUT = Layout()


def object_name(layer: str, path: Path, layout: Layout = DEFAULT_LAYOUT) -> str | None:
    match = re.search(layout.objects[layer], path.read_text(), layout.flags)
    return match.group(match.lastindex or 1) if match else None


def order_by_dependency(
    layer: str, directory: Path, layout: Layout = DEFAULT_LAYOUT
) -> list[Path]:
    """
    Objects another object points at must load first, and file names alone do not
    guarantee that. Tables point at each other through REFERENCES, views through
    FROM and JOIN, and the regexes in the layout say how for any other layer.
    """
    files = sorted(directory.glob("*.sql"))
    by_name = {name: f for f in files if (name := object_name(layer, f, layout))}
    pattern = re.compile(layout.references[layer], layout.flags)
    ordered: list[Path] = []
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        path = by_name[name]
        for dependency in pattern.findall(path.read_text()):
            if dependency != name and dependency in by_name:
                visit(dependency)
        ordered.append(path)

    for name in by_name:
        visit(name)
    return ordered


def layer_files(
    schema_dir: Path, layer: str, layout: Layout = DEFAULT_LAYOUT
) -> list[Path]:
    directory = schema_dir / layer
    if not directory.is_dir():
        return []
    if layer in layout.objects:
        return order_by_dependency(layer, directory, layout)
    return sorted(directory.glob("*.sql"))


def manifest_text(schema_dir: Path, layout: Layout = DEFAULT_LAYOUT) -> str:
    lines = [
        f"\\i {layer}/{path.name}"
        for layer in layout.layers
        for path in layer_files(schema_dir, layer, layout)
    ]
    return "\n".join(lines) + "\n" if lines else ""


def generate_manifest(schema_dir: Path, layout: Layout = DEFAULT_LAYOUT) -> None:
    (schema_dir / "manifest.sql").write_text(manifest_text(schema_dir, layout))


def schema_sql(schema_dir: Path) -> str:
    paths = [
        schema_dir / line.removeprefix("\\i ").strip()
        for line in (schema_dir / "manifest.sql").read_text().splitlines()
        if line.startswith("\\i ")
    ]
    body = "\n".join(path.read_text() for path in paths)
    return f"SET check_function_bodies = off;\n{body}"
