"""Geographic/temporal partition invariants and transparent evaluation metrics."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/evaluate_continent_holdout.py"
spec = importlib.util.spec_from_file_location("continent_holdout", PATH)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def case(country="Brazil", group="South_America", day=None):
    return {"day": m.day(2021) if day is None else day, "gx": 1000, "gy": 1000,
            "country": country, "group": group}


def test_matched_budgets_after_exclusion():
    assert sum(m.allocations(400, list(m.GROUPS)).values()) == 400
    assert sum(m.allocations(400, [g for g in m.GROUPS if g != "Asia"]).values()) == 400
    assert m.allocations(5, ["a", "b"]) == {"a": 3, "b": 2}


def test_holdout_absent_in_every_source_stage():
    with pytest.raises(ValueError, match="Excluded geography"):
        m.validate_partition([case()], "South_America", 2019, 2021)
    m.validate_partition([case("India", "Asia")], "South_America", 2019, 2021)


def test_country_group_mismatch_rejected():
    with pytest.raises(ValueError):
        m.validate_partition([case("India", "Oceania")], None, 2019, 2021)


def test_label_embargo_at_each_stage_boundary():
    with pytest.raises(ValueError, match="temporal"):
        m.validate_partition([case(day=m.day(2022) - 3)], None, 2019, 2021)
    m.validate_partition([case(day=m.day(2022) - 4)], None, 2019, 2021)
    with pytest.raises(ValueError, match="temporal"):
        m.validate_partition([case(day=m.day(2018))], None, 2019, 2021)


def test_exact_duplicate_case_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        m.validate_partition([case(), case()], None, 2019, 2021)


def test_metrics_separate_exact_from_nearby():
    p = np.zeros((5, 5), bool); t = p.copy()
    p[2, 2] = True; t[2, 3] = True
    probabilities = p.astype(float)
    scores = m.mask_metrics(p, t, probabilities, np.ones_like(p))
    assert scores["iou"] == 0 and scores["within_5km"] == 1
    assert scores["precision"] == scores["recall"] == 0
    assert scores["brier"] == pytest.approx(2/25)


def test_empty_cases_explicit_and_macro_averaging():
    empty = np.zeros((3, 3), bool)
    scores = m.mask_metrics(empty, empty, empty.astype(float), np.ones_like(empty))
    assert scores["iou"] == scores["within_5km"] == 1
    assert scores["precision"] is None and scores["recall"] is None


def test_threshold_selection_uses_only_given_calibration_data():
    probabilities = np.full((3, 3, 3), np.nan)
    truth = np.zeros((3, 3, 3), bool)
    probabilities[:, 1, 1] = .21; truth[:, 1, 1] = True
    selected = m.choose_thresholds([(case(), probabilities, truth, np.ones((3, 3)))])
    assert selected == [.05, .05, .05]
    assert all(t in m.THRESHOLDS for t in selected)


def test_empty_source_calibration_is_rejected():
    with pytest.raises(ValueError, match="No source"):
        m.choose_thresholds([])


def test_group_scope_is_five_countries_four_groups():
    assert len(m.GROUPS) == 4 and sum(map(len, m.GROUPS.values())) == 5
    assert "Africa" not in m.GROUPS and "Europe" not in m.GROUPS
