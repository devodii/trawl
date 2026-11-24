import pytest

from trawl.work.stages import Registry, Stage, order, select


def stage(name: str, *needs: str) -> Stage:
    return Stage(id=name, uses=f"x.{name}", needs=list(needs))


def test_stages_run_after_the_stages_they_need():
    stages = [stage("graph", "clone"), stage("clone", "discover"), stage("discover")]

    assert [s.id for s in order(stages)] == ["discover", "clone", "graph"]


def test_a_stage_cycle_is_refused():
    with pytest.raises(ValueError, match="cycle"):
        order([stage("a", "b"), stage("b", "a")])


def test_a_stage_that_needs_nothing_known_is_refused():
    with pytest.raises(ValueError, match="unknown stage x"):
        order([stage("a", "x")])


def test_select_takes_regexes_and_rejects_patterns_that_match_nothing():
    stages = [stage("fetch_a"), stage("fetch_b", "fetch_a"), stage("report", "fetch_b")]

    assert [s.id for s in select(stages, only=["fetch_.*"])] == ["fetch_a", "fetch_b"]
    assert [s.id for s in select(stages, skip=["report"])] == ["fetch_a", "fetch_b"]
    assert [s.id for s in select(stages, start="fetch_b")] == ["fetch_b", "report"]
    with pytest.raises(ValueError, match="unknown stage nope"):
        select(stages, only=["nope"])


def test_extra_fields_and_with_options_are_kept():
    item = Stage(**{"id": "a", "uses": "x.a", "with": {"limit": 3}, "retries": 2})

    assert item.options == {"limit": 3}
    assert item.model_extra == {"retries": 2}


async def test_the_registry_maps_names_to_functions():
    registry = Registry()
    seen = []

    @registry.stage("x.a")
    async def run(context, item):
        seen.append(item.id)

    await registry.get("x.a")(None, stage("a"))

    assert seen == ["a"] and registry.get("missing") is None
