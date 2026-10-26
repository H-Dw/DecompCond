import pytest
from decompcond.paths import compile_path, validate_batch
from decompcond.io import InvalidProposal


def proposal(segments, name="test"):
    return dict(
        id=name,
        segments=segments,
        parents=[],
        evidence=["condition_library"],
        observation="Observation",
        change="Change",
        hypothesis="Hypothesis",
    )


def test_empty_release_reentry_and_canonical_identity():
    groups = {"C1": {}, "C2": {}}
    p = compile_path(
        proposal(
            [
                dict(start=0, end=20, active=["C1"]),
                dict(start=20, end=80, active=[]),
                dict(start=80, end=100, active=["C1"]),
            ]
        ),
        groups,
        100,
        10,
    )
    assert p["expanded"][20] == [] and p["expanded"][80] == ["C1"]
    a = compile_path(
        proposal([dict(start=0, end=100, active=["C1", "C2"])]), groups, 100, 10
    )
    b = compile_path(
        proposal(
            [
                dict(start=0, end=40, active=["C2", "C1"]),
                dict(start=40, end=100, active=["C1", "C2"]),
            ]
        ),
        groups,
        100,
        10,
    )
    assert a["schedule_hash"] == b["schedule_hash"]


@pytest.mark.parametrize(
    "segments",
    [
        [dict(start=0, end=25, active=[]), dict(start=25, end=100, active=[])],
        [dict(start=10, end=100, active=[])],
        [dict(start=0, end=90, active=[])],
        [dict(start=0, end=100, active=["UNKNOWN"])],
        [dict(start=0, end=100, active=["C1", "C1"])],
    ],
)
def test_invalid_schedule(segments):
    with pytest.raises(InvalidProposal):
        compile_path(proposal(segments), {"C1": {}}, 100, 10)


def test_duplicate_history_and_unknown_evidence():
    p = proposal([dict(start=0, end=100, active=["C1"])])
    compiled = compile_path(p, {"C1": {}}, 100, 10)
    with pytest.raises(InvalidProposal):
        validate_batch(
            [p],
            {"groups": {"C1": {}}},
            100,
            10,
            1,
            {compiled["schedule_hash"]: {"path": compiled, "summary": {"J": 0}}},
            {"condition_library"},
        )
    p["evidence"] = ["invented"]
    with pytest.raises(InvalidProposal):
        validate_batch([p], {"groups": {"C1": {}}}, 100, 10, 1, {}, set())
