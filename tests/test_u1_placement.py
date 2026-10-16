"""Pattern rotation, printer-axis proposals and the hypothetical U1 side camera."""

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from aligncam.bench.runner import run_case
from aligncam.bench.sweeps import sweep_cases
from aligncam.config import MeasureConfig, PatternConfig, PrinterConfig, load_config
from aligncam.geometry import build_layout
from aligncam.measure import measure_image
from aligncam.measure.estimate import Measurement, ToolOffset
from aligncam.pattern import generate_gcode
from aligncam.pattern.gcode import check_bounds
from aligncam.printer.offsets import build_proposal
from aligncam.sim import Scenario, simulate
from aligncam.sim.scenario import scene_camera
from aligncam.sim.u1 import PLACEMENTS, side_camera_pose


def test_rotation_maps_pattern_axes_to_bed():
    lay = build_layout(PatternConfig(center_on_bed_mm=(200.0, 135.0), rotation_deg=90))
    assert np.allclose(lay.to_bed((10.0, 0.0)), (200.0, 145.0))
    assert np.allclose(lay.vector_to_bed((0.1, 0.0)), (0.0, 0.1))
    x0, y0, x1, y1 = lay.bbox()
    bx0, by0, bx1, by1 = lay.bed_bbox()
    assert np.isclose(bx1 - bx0, y1 - y0) and np.isclose(by1 - by0, x1 - x0)


def test_rotation_must_be_a_right_angle():
    with pytest.raises(ValidationError):
        PatternConfig(rotation_deg=45)


def test_rotated_pattern_fits_near_the_edge_only_when_rotated():
    printer = PrinterConfig()
    near = PLACEMENTS[-1]
    check_bounds(build_layout(near.pattern_config()), printer)
    unrotated = near.pattern_config().model_copy(update={"rotation_deg": 0})
    with pytest.raises(ValueError, match="outside"):
        check_bounds(build_layout(unrotated), printer)


def test_u1_side_overlay_config():
    root = Path(__file__).resolve().parents[1] / "config"
    cfg = load_config(root / "pattern.default.yaml", root / "placements" / "u1-side.yaml")
    assert cfg.pattern.rotation_deg == 90
    check_bounds(build_layout(cfg.pattern), cfg.printer)


def test_rotated_gcode_moves_stay_on_bed():
    lay = build_layout(PLACEMENTS[-1].pattern_config())
    gcode = generate_gcode(lay, PrinterConfig())
    assert "rotated 90 deg" in gcode
    moves = [ln.split() for ln in gcode.splitlines() if ln.startswith(("G0 ", "G1 "))]
    xs = [float(t[1:]) for words in moves for t in words if t.startswith("X")]
    assert min(xs) >= 0 and max(xs) <= 270


def test_proposal_is_in_printer_axes():
    m = Measurement("ok", [], [], None, [], {1: ToolOffset(1, "ok", [], 0.1, -0.02, 0.004, 0.009)})
    c = build_proposal(m, sign=-1, pattern_rotation_deg=90).corrections[0]
    # Pattern +X is bed +Y and pattern +Y is bed -X after a 90 deg rotation.
    assert (c.measured_dx_mm, c.measured_dy_mm) == (0.02, 0.1)
    assert (c.sigma_x_mm, c.sigma_y_mm) == (0.009, 0.004)
    assert (c.delta_x_mm, c.delta_y_mm) == (-0.02, -0.1)


def test_side_camera_pose_measures_offsets():
    placement = PLACEMENTS[2]
    lay = build_layout(placement.pattern_config())
    sc = Scenario(seed=3, pose=side_camera_pose(150.0, 30.0, 100.0))
    cam = scene_camera(sc, lay)
    assert 0.15 < cam.gsd_at((0.0, 0.0)) < 0.35
    assert 25 < cam.view_angle_deg((0.0, 0.0)) < 45
    m = measure_image(simulate(sc, lay).image, lay, MeasureConfig())
    for t, (dx, dy) in sc.offsets_mm.items():
        to = m.tools[t]
        assert to.status == "ok", to.reasons
        assert abs(to.dx_mm - dx) < 0.04 and abs(to.dy_mm - dy) < 0.04


def test_pattern_outside_the_frame_is_not_rendered():
    lay = build_layout(PLACEMENTS[0].pattern_config())
    # Narrow lens aimed at the far corner: the bed centre is out of view.
    sc = Scenario(seed=0, pose=side_camera_pose(100.0, 30.0, 40.0, aim_bed_mm=(270.0, 0.0)))
    m = measure_image(simulate(sc, lay).image, lay, MeasureConfig())
    assert m.status == "rejected"


def test_u1_benchmark_case():
    case = next(c for c in sweep_cases(1) if c[0] == "u1")
    rows = run_case(case)
    assert len(rows) == 6
    assert all(r["cam_height_mm"] != "" and r["view_deg"] > 0 for r in rows)
