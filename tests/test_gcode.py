import re

import pytest

from aligncam.config import PrinterConfig
from aligncam.pattern import generate_gcode, layout_svg
from aligncam.pattern.gcode import extrusion_per_mm

MOVE = re.compile(r"^G[01] .*X(?P<x>-?[\d.]+) Y(?P<y>-?[\d.]+)")


def test_structure(layout):
    g = generate_gcode(layout)
    lines = g.splitlines()
    assert "M83 ; relative extrusion" in lines
    tool_lines = [ln for ln in lines if re.fullmatch(r"T\d", ln)]
    assert tool_lines == ["T0", "T1", "T2", "T3"]
    assert g.count("TO VERIFY ON HARDWARE") >= 3


def test_moves_inside_bed(layout):
    printer = PrinterConfig()
    n = 0
    for ln in generate_gcode(layout, printer).splitlines():
        m = MOVE.match(ln)
        if m:
            n += 1
            assert 0 <= float(m["x"]) <= printer.bed_size_mm[0]
            assert 0 <= float(m["y"]) <= printer.bed_size_mm[1]
    assert n > 500


def test_extrusion_amount(layout):
    printer = PrinterConfig()
    e = extrusion_per_mm(0.45, 0.25, printer)
    assert 0.02 < e < 0.05
    g = generate_gcode(layout, printer)
    total = sum(float(x) for x in re.findall(r" E(\d[\d.]*) F1200", g))
    assert total > 0


def test_custom_template(layout):
    printer = PrinterConfig(gcode_tool_change="MY_TOOLCHANGE TOOL={{ tool }}\n")
    assert "MY_TOOLCHANGE TOOL=3" in generate_gcode(layout, printer)


def test_outside_bed_is_refused(layout):
    with pytest.raises(ValueError, match="outside"):
        generate_gcode(layout, PrinterConfig(bed_size_mm=(100.0, 100.0)))


def test_svg(layout):
    svg = layout_svg(layout)
    assert svg.startswith("<svg") and svg.count("<polyline") > 100
