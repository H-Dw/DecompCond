import pytest
from decompcond.feedback import summarize, best_record, reentry_steps
from decompcond.io import TechnicalFailure


def test_failed_attempts_remain_in_denominator_and_technical_missing_is_not_zero():
    path = {"id": "x", "schedule_hash": "x", "expanded": [[]]}
    good = dict(
        status="evaluated",
        structure_pass=True,
        effective_recovery=1.0,
        delta_pic50=2.0,
        condition_metrics={},
        pb_checks={},
    )
    failed = dict(
        status="generation_failure",
        structure_pass=False,
        effective_recovery=0.0,
        delta_pic50=None,
        condition_metrics={},
        pb_checks={},
    )
    summary = summarize(path, [good, failed], {"groups": {}})
    assert (
        summary["J"] == 0.5
        and summary["attempts"] == 2
        and summary["valid_delta_pic50"]["n"] == 1
    )
    with pytest.raises(TechnicalFailure):
        summarize(path, [good, {"status": "technical_failure"}], {"groups": {}})


def test_path_tie_break_affinity():
    records = [
        {
            "summary": {
                "structure_pass_count": 1,
                "J": 0.5,
                "valid_delta_pic50": {"mean": x},
            }
        }
        for x in [-1.0, 2.0]
    ]
    assert best_record(records) is records[1]


def test_first_activation_and_release_are_not_reentries():
    path = {"expanded": [["C1"], ["C1"], [], ["C2"], ["C1", "C2"], [], ["C2"]]}
    assert reentry_steps(path) == {4, 6}
