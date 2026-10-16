"""SVG preview of the pattern (pattern frame, mm units, Y up)."""

from __future__ import annotations

from aligncam.config import PrinterConfig
from aligncam.geometry import Layout


def layout_svg(
    layout: Layout, printer: PrinterConfig | None = None, bed_hex: str = "#2b2b2b"
) -> str:
    printer = printer or PrinterConfig()
    x0, y0, x1, y1 = layout.bbox()
    pad = 4.0
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    w = layout.line_width
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.2f} {-y1:.2f} '
        f'{x1 - x0:.2f} {y1 - y0:.2f}" width="{(x1 - x0) * 6:.0f}" height="{(y1 - y0) * 6:.0f}">',
        f'<rect x="{x0:.2f}" y="{-y1:.2f}" width="{x1 - x0:.2f}" height="{y1 - y0:.2f}" '
        f'fill="{bed_hex}"/>',
        '<g transform="scale(1,-1)" stroke-linecap="round" fill="none">',
    ]
    for tool in layout.print_order:
        color = printer.filaments[tool].color_hex
        for d in [*layout.fiducials, layout.orientation_dot]:
            if d.tool == tool:
                parts.append(
                    f'<circle cx="{d.center[0]:.3f}" cy="{d.center[1]:.3f}" '
                    f'r="{d.diameter / 2:.3f}" fill="{color}"/>'
                )
        for path in layout.toolpaths(tool):
            if path.role in ("fiducial", "orientation"):
                continue
            pts = " ".join(f"{x:.3f},{y:.3f}" for x, y in path.points)
            parts.append(f'<polyline points="{pts}" stroke="{color}" stroke-width="{w:.3f}"/>')
    parts.append("</g>")
    for cell in layout.cells:
        bx0, _, _, by1 = cell.bbox(w)
        parts.append(
            f'<text x="{bx0:.2f}" y="{-by1 - 0.8:.2f}" font-size="2.2" fill="#bbbbbb" '
            f'font-family="sans-serif">{cell.name}{" (control)" if cell.is_control else ""}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
