import math

from aligncam.bench.report import TARGET_MM, camera_table, stats
from aligncam.bench.runner import run_case
from aligncam.bench.sweeps import SWEEPS, sweep_cases


def _row(err, acc=1, sig=0.01):
    return {"error_mm": err, "accepted": acc, "sigma_mm": sig}


def test_stats_counts_unsafe_and_rejections():
    rows = [_row(0.001), _row(0.002), _row(0.05), _row(math.nan, acc=0)]
    s = stats(rows)
    assert s.n == 4
    assert s.accepted == 0.75
    assert s.unsafe == 0.25  # one accepted error above TARGET_MM
    assert 0.05 > TARGET_MM


def test_sweep_cases_cover_all_sweeps():
    names = {c[0] for c in sweep_cases(1)}
    assert set(SWEEPS) <= names and {"grid", "colors"} <= names


def test_run_case_rows():
    case = next(iter(sweep_cases(1)))
    rows = run_case(case)
    assert len(rows) == 6
    assert {r["axis"] for r in rows} == {"x", "y"}


def test_camera_table_shape():
    t = camera_table(0.1)
    assert t.count("\n") == 5 and "mm" in t
