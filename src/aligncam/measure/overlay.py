"""Draw the detection result on top of the input image."""

from __future__ import annotations

import cv2
import numpy as np

from aligncam.geometry import Grating, Layout
from aligncam.measure.estimate import Measurement
from aligncam.measure.grating import GratingReading

OK = (80, 200, 80)
BAD = (60, 60, 230)
FID = (255, 200, 0)


def _to_img(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return cv2.perspectiveTransform(np.asarray(pts, float).reshape(-1, 1, 2), H).reshape(-1, 2)


def _line_ticks(img, H, g: Grating, reading: GratingReading, color, scale: int) -> None:
    h = g.line_length / 2.0
    for u in reading.line_centers_u:
        if not np.isfinite(u):
            continue
        if g.axis == "x":
            seg = [(g.center[0] + u, g.center[1] - h), (g.center[0] + u, g.center[1] + h)]
        else:
            seg = [(g.center[0] - h, g.center[1] + u), (g.center[0] + h, g.center[1] + u)]
        a, b = _to_img(H, seg) * scale
        cv2.line(
            img,
            tuple(np.round(a).astype(int)),
            tuple(np.round(b).astype(int)),
            color,
            1,
            cv2.LINE_AA,
        )


def draw_overlay(img_bgr: np.ndarray, layout: Layout, m: Measurement, scale: int = 2) -> np.ndarray:
    """Upscaled copy of the image with fiducials, cell outlines, line centres and results."""
    out = cv2.resize(img_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    if m.fiducials is None:
        cv2.putText(
            out,
            "REJECTED: fiducials not found",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            BAD,
            2,
            cv2.LINE_AA,
        )
        return out
    H = m.fiducials.H_img_from_mm
    r = m.fiducials.disk_radius_px * scale
    for p in m.fiducials.image_points:
        c = tuple(np.round(p * scale).astype(int))
        cv2.circle(out, c, int(r * 1.15), FID, 2, cv2.LINE_AA)
        cv2.drawMarker(out, c, FID, cv2.MARKER_CROSS, int(r), 1, cv2.LINE_AA)

    cells = {c.name: c for c in layout.cells}
    for res in m.cells:
        cell = cells[res.name]
        color = OK if res.status == "ok" else BAD
        x0, y0, x1, y1 = cell.bbox(layout.line_width)
        box = _to_img(H, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]) * scale
        cv2.polylines(out, [np.round(box).astype(np.int32)], True, color, 1, cv2.LINE_AA)
        for g, reading in zip(cell.references, res.references, strict=False):
            _line_ticks(out, H, g, reading, (255, 255, 0), scale)
        if res.measured is not None:
            _line_ticks(out, H, cell.measured, res.measured, (255, 0, 255), scale)
        label = res.name + (" ctrl" if res.is_control else "")
        if res.offset_mm is not None:
            label += f" {res.offset_mm * 1000:+.0f}um"
        anchor = np.round(box[3] + np.array([0, -6])).astype(int)
        cv2.putText(
            out, label, tuple(anchor), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA
        )

    y = 24
    for t in m.tools.values():
        if t.status == "ok":
            text = (
                f"T{t.tool}: dx {t.dx_mm * 1000:+.0f} +/- {t.sigma_x_mm * 1000:.0f} um, "
                f"dy {t.dy_mm * 1000:+.0f} +/- {t.sigma_y_mm * 1000:.0f} um"
            )
        else:
            text = f"T{t.tool}: REJECTED ({', '.join(t.reasons)})"
        cv2.putText(out, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(
            out,
            text,
            (10, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            OK if t.status == "ok" else BAD,
            1,
            cv2.LINE_AA,
        )
        y += 24
    return out
