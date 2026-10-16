"""Render the printed pattern as a top-down colour texture in millimetre space.

The model is deliberately simple but captures what matters for the measurement:

* every extrusion is a bead with a rounded profile and soft, anti-aliased edges;
* bead centre wobbles along its length and every line has its own random
  placement error (mechanical and extrusion noise);
* the bead width fluctuates along the line, each tool can over/under-extrude,
  and lines may start with a blob;
* low filament/bed contrast is modelled by pulling the filament colour towards
  the bed colour;
* bead height produces directional shading (light from one side).

Tool offsets are applied to every element printed by that tool.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from pydantic import BaseModel, Field

from aligncam.geometry import Layout, Path


def hex_to_rgb(color: str) -> np.ndarray:
    c = color.lstrip("#")
    return np.array([int(c[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.float32) / 255.0


class Appearance(BaseModel):
    bed_rgb: tuple[float, float, float] = (0.17, 0.17, 0.18)
    bed_texture: float = 0.06
    """Relative amplitude of the bed surface texture (textured PEI-like plates)."""
    tool_rgb: dict[int, tuple[float, float, float]] = Field(
        default_factory=lambda: {
            0: (0.91, 0.91, 0.91),
            1: (0.78, 0.20, 0.20),
            2: (0.16, 0.36, 0.82),
            3: (0.91, 0.72, 0.13),
        }
    )
    contrast: dict[int, float] = Field(default_factory=dict)
    """Per tool, 1 = nominal colour, 0 = same colour as the bed. Missing = 1."""
    line_width_mm: float = 0.45
    width_jitter: float = 0.06
    """Relative std of the bead width along a line."""
    width_scale: dict[int, float] = Field(default_factory=dict)
    """Per-tool systematic over/under extrusion (1 = nominal)."""
    wobble_mm: float = 0.01
    """Std of the low-frequency lateral wobble of the bead centre."""
    wobble_corr_mm: float = 2.0
    line_jitter_mm: float = 0.01
    """Std of the per-line placement error."""
    start_blob: float = 0.3
    """Extra relative width at the start of every line (ooze)."""
    height_shading: float = 0.25
    light_dir: tuple[float, float] = (-0.6, 0.8)


@dataclass
class Texture:
    rgb: np.ndarray
    """(H, W, 3) float32, roughly linear 0..1."""
    tex_from_mm: np.ndarray
    """3x3 affine: pattern mm -> texture pixel (pixel centres at integers)."""
    px_per_mm: float


def _smooth_noise(length: float, corr: float, std: float, rng: np.random.Generator):
    if std <= 0:
        return np.array([0.0, max(length, 1e-6)]), np.zeros(2)
    step = max(corr / 2.0, 0.05)
    n = max(2, int(math.ceil(length / step)) + 2)
    t = np.linspace(-step, length + step, n)
    v = rng.normal(0.0, std, n)
    return t, v


def _stamp_segment(
    cov: np.ndarray,
    height: np.ndarray,
    a: np.ndarray,
    b: np.ndarray,
    width: float,
    tex: Texture,
    app: Appearance,
    rng: np.random.Generator,
    blob: bool,
) -> None:
    s = tex.px_per_mm
    A = tex.tex_from_mm
    seg = b - a
    length = float(np.hypot(*seg))
    u = seg / length if length > 0 else np.array([1.0, 0.0])
    n = np.array([-u[1], u[0]])
    jitter = rng.normal(0.0, app.line_jitter_mm) if app.line_jitter_mm > 0 else 0.0
    wt, wv = _smooth_noise(length, app.wobble_corr_mm, app.wobble_mm, rng)
    gt, gv = _smooth_noise(length, app.wobble_corr_mm, app.width_jitter, rng)
    reach = width * (1.0 + app.start_blob) / 2.0 + abs(jitter) + 4 * app.wobble_mm + 2.0 / s
    pts = np.array(
        [
            a - reach * (u + n),
            a - reach * u + reach * n,
            b + reach * (u + n),
            b + reach * u - reach * n,
        ]
    )
    pix = (A[:2, :2] @ pts.T).T + A[:2, 2]
    c0, r0 = np.floor(pix.min(axis=0)).astype(int)
    c1, r1 = np.ceil(pix.max(axis=0)).astype(int) + 1
    c0, r0 = max(c0, 0), max(r0, 0)
    c1, r1 = min(c1, cov.shape[1]), min(r1, cov.shape[0])
    if c1 <= c0 or r1 <= r0:
        return
    cols = np.arange(c0, c1, dtype=np.float32)
    rows = np.arange(r0, r1, dtype=np.float32)
    # texture pixel -> mm (A is axis-aligned: x = (col - A02)/A00, y = (row - A12)/A11)
    x = (cols - A[0, 2]) / A[0, 0]
    y = (rows - A[1, 2]) / A[1, 1]
    X, Y = np.meshgrid(x, y)
    px, py = X - a[0], Y - a[1]
    t = px * u[0] + py * u[1]
    d = px * n[0] + py * n[1]
    tc = np.clip(t, 0.0, length)
    centre = jitter + np.interp(tc, wt, wv)
    w = width * (1.0 + np.interp(tc, gt, gv))
    if blob and app.start_blob > 0:
        w = w * (1.0 + app.start_blob * np.exp(-((tc / 0.4) ** 2)))
    along = np.where(t < 0, -t, np.where(t > length, t - length, 0.0))
    dist = np.hypot(d - centre, along)
    half = np.maximum(w / 2.0, 1e-3)
    c = np.clip((half - dist) * s + 0.5, 0.0, 1.0)
    h = np.sqrt(np.clip(1.0 - (dist / half) ** 2, 0.0, 1.0))
    np.maximum(cov[r0:r1, c0:c1], c, out=cov[r0:r1, c0:c1])
    np.maximum(height[r0:r1, c0:c1], h, out=height[r0:r1, c0:c1])


def _stamp_disk(cov, height, center, radius, line_width, tex: Texture) -> None:
    s = tex.px_per_mm
    A = tex.tex_from_mm
    cpx = A[:2, :2] @ np.asarray(center) + A[:2, 2]
    rpx = radius * s + 3
    c0, r0 = (np.floor(cpx - rpx)).astype(int)
    c1, r1 = (np.ceil(cpx + rpx)).astype(int) + 1
    c0, r0 = max(c0, 0), max(r0, 0)
    c1, r1 = min(c1, cov.shape[1]), min(r1, cov.shape[0])
    x = (np.arange(c0, c1, dtype=np.float32) - A[0, 2]) / A[0, 0]
    y = (np.arange(r0, r1, dtype=np.float32) - A[1, 2]) / A[1, 1]
    X, Y = np.meshgrid(x, y)
    dist = np.hypot(X - center[0], Y - center[1])
    c = np.clip((radius - dist) * s + 0.5, 0.0, 1.0)
    h = np.sqrt(np.clip((radius - dist) / (line_width / 2.0), 0.0, 1.0))
    np.maximum(cov[r0:r1, c0:c1], c, out=cov[r0:r1, c0:c1])
    np.maximum(height[r0:r1, c0:c1], h, out=height[r0:r1, c0:c1])


def render_texture(
    layout: Layout,
    offsets: dict[int, tuple[float, float]],
    app: Appearance,
    rng: np.random.Generator,
    px_per_mm: float = 20.0,
    margin_mm: float = 12.0,
) -> Texture:
    """Top-down texture of the printed pattern with the given per-tool XY errors."""
    x0, y0, x1, y1 = layout.bbox()
    x0, y0, x1, y1 = x0 - margin_mm, y0 - margin_mm, x1 + margin_mm, y1 + margin_mm
    s = float(px_per_mm)
    W = int(math.ceil((x1 - x0) * s))
    H = int(math.ceil((y1 - y0) * s))
    # col = (x - x0) * s - 0.5 ; row = (y1 - y) * s - 0.5
    A = np.array([[s, 0, -x0 * s - 0.5], [0, -s, y1 * s - 0.5], [0, 0, 1.0]])
    tex = Texture(np.empty((H, W, 3), np.float32), A, s)

    bed = np.asarray(app.bed_rgb, np.float32)
    # Bed surface: low-frequency mottling plus fine grain.
    low = cv2.resize(
        rng.normal(0, 1, (max(H // 40, 2), max(W // 40, 2))).astype(np.float32),
        (W, H),
        interpolation=cv2.INTER_CUBIC,
    )
    grain = cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 0.6 * s / 20)
    bed_mod = 1.0 + app.bed_texture * (0.7 * low + 0.6 * grain)
    rgb = bed[None, None, :] * bed_mod[..., None]

    height = np.zeros((H, W), np.float32)
    cov = np.zeros((H, W), np.float32)
    for tool in layout.print_order:
        cov[:] = 0.0
        off = np.asarray(offsets.get(tool, (0.0, 0.0)), float)
        width = app.line_width_mm * app.width_scale.get(tool, 1.0)
        for d in [*layout.fiducials, layout.orientation_dot]:
            if d.tool == tool:
                _stamp_disk(
                    cov,
                    height,
                    np.asarray(d.center) + off,
                    d.diameter / 2.0,
                    app.line_width_mm,
                    tex,
                )
        for path in layout.toolpaths(tool):
            if path.role in ("fiducial", "orientation"):
                continue
            _stamp_path(cov, height, path, off, width, tex, app, rng)
        k = app.contrast.get(tool, 1.0)
        color = bed + k * (np.asarray(app.tool_rgb[tool], np.float32) - bed)
        rgb = rgb * (1.0 - cov[..., None]) + color[None, None, :] * cov[..., None]

    if app.height_shading > 0:
        hs = cv2.GaussianBlur(height, (0, 0), 0.08 * s)
        gx = cv2.Sobel(hs, cv2.CV_32F, 1, 0, ksize=3) / 8.0 * s * layout.line_width
        gy = -cv2.Sobel(hs, cv2.CV_32F, 0, 1, ksize=3) / 8.0 * s * layout.line_width
        lx, ly = app.light_dir
        norm = math.hypot(lx, ly) or 1.0
        shade = 1.0 + app.height_shading * (gx * lx + gy * ly) / norm
        rgb = rgb * np.clip(shade, 0.3, 2.0)[..., None]

    tex.rgb = np.clip(rgb, 0.0, 1.5).astype(np.float32)
    return tex


def _stamp_path(cov, height, path: Path, off, width, tex, app, rng) -> None:
    pts = np.asarray(path.points, float) + off
    for i, (a, b) in enumerate(zip(pts[:-1], pts[1:], strict=True)):
        _stamp_segment(
            cov, height, a, b, width, tex, app, rng, blob=(i == 0 and path.role == "grating")
        )
