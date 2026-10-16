import numpy as np

from aligncam.config import PatternConfig
from aligncam.geometry import build_layout


def _disjoint(a, b):
    return a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]


def test_cells_per_tool_and_axis(layout):
    names = sorted(c.name for c in layout.cells)
    assert names == sorted(f"T{t}-{a}" for t in (0, 1, 2, 3) for a in "xy")
    assert [c.name for c in layout.cells if c.is_control] == ["T0-x", "T0-y"]


def test_central_lines_on_cell_centre(layout):
    for cell in layout.cells:
        c = cell.center[0] if cell.axis == "x" else cell.center[1]
        for g in (cell.reference, cell.measured):
            pos = g.positions()
            assert len(pos) % 2 == 1
            assert np.isclose(pos[len(pos) // 2], c)
            assert np.allclose(np.diff(pos), g.pitch)


def test_vernier_pitch(layout):
    cfg = layout.config
    for cell in layout.cells:
        assert np.isclose(cell.measured.pitch, cfg.pitch_mm * cfg.vernier_ratio)
        assert cell.reference.tool == cfg.reference_tool


def test_cells_do_not_overlap_each_other_or_fiducials(layout):
    boxes = [c.bbox(layout.line_width) for c in layout.cells]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert _disjoint(a, b)
    area = layout.cell_area()
    for d in [*layout.fiducials, layout.orientation_dot]:
        r = d.diameter / 2
        cx, cy = d.center
        assert _disjoint(area, (cx - r, cy - r, cx + r, cy + r))


def test_fiducial_order_tl_tr_br_bl(layout):
    p = layout.fiducial_points()
    assert p[0][0] < p[1][0] and p[0][1] > p[3][1]
    assert p[2][0] > p[3][0] and p[1][1] > p[2][1]


def test_halves_do_not_touch(layout):
    w = layout.line_width
    for cell in layout.cells:
        assert _disjoint(cell.reference.bbox(w), cell.measured.bbox(w))


def test_toolpaths_cover_every_tool(layout):
    for tool in layout.print_order:
        assert "grating" in {p.role for p in layout.toolpaths(tool)}
    assert "fiducial" in {p.role for p in layout.toolpaths(0)}


def test_no_control_cell():
    layout = build_layout(PatternConfig(include_control=False))
    assert not any(c.is_control for c in layout.cells)
    assert len(layout.cells) == 6
