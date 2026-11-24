import re
import tomllib
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field


class CargoSettings(BaseModel):
    """What to look for in a Cargo manifest. The package name and the lock filter are regexes."""

    model_config = ConfigDict(extra="allow")

    sdk: str = r"soroban-sdk"
    interesting: str = r"^(soroban-|stellar-|loam|openzeppelin)"


DEFAULT = CargoSettings()


class Dependency(BaseModel):
    kind: str
    name: str
    requirement: str | None = None
    source: str = "registry"
    git_url: str | None = None
    local_path: str | None = None
    features: list[str] = Field(default_factory=list)
    optional: bool = False


class CrateInfo(BaseModel):
    path: str
    name: str | None = None
    version: str | None = None
    edition: str | None = None
    is_workspace_root: bool = False
    workspace_members: list[str] = Field(default_factory=list)
    is_contract: bool = False
    crate_types: list[str] = Field(default_factory=list)
    soroban_sdk_requirement: str | None = None
    soroban_sdk_resolved: str | None = None
    features: dict[str, list[str]] = Field(default_factory=dict)
    profiles: dict[str, dict] = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    dependencies: list[Dependency] = Field(default_factory=list)

    def depends_on(self, name: str) -> bool:
        return any(d.name == name for d in self.dependencies)


def literal(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): literal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [literal(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def dependency(
    kind: str, name: str, spec: object, workspace: dict[str, object]
) -> Dependency:
    if isinstance(spec, str):
        return Dependency(kind=kind, name=name, requirement=spec)
    if not isinstance(spec, dict):
        return Dependency(kind=kind, name=name)
    inherited: dict = {}
    source = "registry"
    if spec.get("workspace"):
        source = "workspace"
        base = workspace.get(name)
        inherited = (
            base
            if isinstance(base, dict)
            else {"version": base}
            if isinstance(base, str)
            else {}
        )
    merged = {**inherited, **spec}
    if merged.get("git"):
        source = "git"
    elif merged.get("path"):
        source = "path"
    return Dependency(
        kind=kind,
        name=str(merged.get("package", name)),
        requirement=merged.get("version"),
        source=source,
        git_url=merged.get("git"),
        local_path=merged.get("path"),
        features=[
            *(inherited.get("features") or []),
            *(spec.get("features") or []),
        ],
        optional=bool(spec.get("optional", False)),
    )


def parse_cargo(
    path: str,
    text: str,
    workspace_dependencies: dict[str, object] | None = None,
    settings: CargoSettings = DEFAULT,
) -> CrateInfo | None:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    package = data.get("package", {})
    workspace = data.get("workspace", {})
    inherited = {**(workspace_dependencies or {}), **workspace.get("dependencies", {})}
    info = CrateInfo(
        path=str(PurePosixPath(path).parent) if "/" in path else ".",
        name=package.get("name"),
        version=package.get("version")
        if isinstance(package.get("version"), str)
        else None,
        edition=package.get("edition")
        if isinstance(package.get("edition"), str)
        else None,
        is_workspace_root="workspace" in data,
        workspace_members=[str(m) for m in workspace.get("members", [])],
        crate_types=[str(t) for t in data.get("lib", {}).get("crate-type", [])],
        features={
            k: [str(i) for i in v]
            for k, v in data.get("features", {}).items()
            if isinstance(v, list)
        },
        profiles=literal(data.get("profile", {})),
        metadata=literal(package.get("metadata", {})),
    )
    for table, kind in (
        ("dependencies", "normal"),
        ("dev-dependencies", "dev"),
        ("build-dependencies", "build"),
    ):
        for name, spec in data.get(table, {}).items():
            info.dependencies.append(dependency(kind, name, spec, inherited))
    for target in data.get("target", {}).values():
        for table, kind in (("dependencies", "normal"), ("dev-dependencies", "dev")):
            for name, spec in target.get(table, {}).items():
                info.dependencies.append(dependency(kind, name, spec, inherited))
    sdk = next(
        (
            d
            for d in info.dependencies
            if re.fullmatch(settings.sdk, d.name) and d.kind == "normal"
        ),
        None,
    ) or next(
        (d for d in info.dependencies if re.fullmatch(settings.sdk, d.name)), None
    )
    info.soroban_sdk_requirement = sdk.requirement if sdk else None
    info.is_contract = "cdylib" in info.crate_types and any(
        re.fullmatch(settings.sdk, d.name) for d in info.dependencies
    )
    return info


def parse_lock(text: str, settings: CargoSettings = DEFAULT) -> dict[str, str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    versions: dict[str, str] = {}
    for package in data.get("package", []):
        name, version = package.get("name", ""), package.get("version", "")
        if re.search(settings.interesting, name) and name not in versions:
            versions[name] = version
    return versions
