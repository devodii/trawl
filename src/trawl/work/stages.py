import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Stage(BaseModel):
    """One step of a workflow. `with` options and any extra field are kept as given."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    uses: str
    needs: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict, alias="with")


StageFunction = Callable[[Any, Stage], Awaitable[None]]


class Registry:
    """Maps a `uses` name to the async function that does the work."""

    def __init__(self) -> None:
        self.functions: dict[str, StageFunction] = {}

    def stage(self, name: str) -> Callable[[StageFunction], StageFunction]:
        def register(function: StageFunction) -> StageFunction:
            self.functions[name] = function
            return function

        return register

    def get(self, name: str) -> StageFunction | None:
        return self.functions.get(name)


def order(stages: list[Stage]) -> list[Stage]:
    """Stages after the stages they need, in the order they were written otherwise."""
    by_id = {s.id: s for s in stages}
    ordered: list[Stage] = []
    seen: set[str] = set()

    def visit(item: Stage, trail: tuple[str, ...]) -> None:
        if item.id in trail:
            raise ValueError(f"stage cycle through {item.id}")
        if item.id in seen:
            return
        for need in item.needs:
            if need not in by_id:
                raise ValueError(f"{item.id} needs unknown stage {need}")
            visit(by_id[need], (*trail, item.id))
        seen.add(item.id)
        ordered.append(item)

    for item in stages:
        visit(item, ())
    return ordered


def matching(ids: list[str], patterns: list[str]) -> set[str]:
    """Every id that fully matches one of the regexes. A plain name matches itself."""
    found: set[str] = set()
    for pattern in patterns:
        hits = {i for i in ids if re.fullmatch(pattern, i)}
        if not hits:
            raise ValueError(f"unknown stage {pattern}, choose from {', '.join(ids)}")
        found |= hits
    return found


def select(
    stages: list[Stage],
    only: list[str] | None = None,
    skip: list[str] | None = None,
    start: str | None = None,
) -> list[Stage]:
    """Narrow a run. `only`, `skip` and `start` are regexes over stage ids."""
    ordered = order(stages)
    ids = [s.id for s in ordered]
    if start:
        first = min(ids.index(i) for i in matching(ids, [start]))
        ordered = ordered[first:]
    if only:
        wanted = matching(ids, only)
        ordered = [s for s in ordered if s.id in wanted]
    if skip:
        left_out = matching(ids, skip)
        ordered = [s for s in ordered if s.id not in left_out]
    return ordered
