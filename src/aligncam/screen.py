"""Pattern and ChArUco board shown on a screen, for tests with a real camera and no printer.

A tablet or phone displays the pattern with known tool offsets; a real camera
photographs it and ``aligncam measure --truth`` compares the result with the truth.
This exercises the real lens, focus, exposure, noise and compression, but not the
look of extruded filament on a plate, so results must be labelled
"real camera, pattern on a screen".

The displayed size does not need to match a real print: the measurement fits a
homography to the fiducials, so a uniformly scaled pattern reads like the same
pattern seen from farther away, and offsets come out in pattern millimetres.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from aligncam.calib.board import rasterize
from aligncam.config import CharucoBoardConfig
from aligncam.geometry import Layout
from aligncam.sim.render import Appearance, render_texture

SCREEN_MARGIN_MM = 6.0
"""Plain border around the pattern, in pattern millimetres."""

CLEAN = Appearance(
    bed_texture=0.0,
    width_jitter=0.0,
    wobble_mm=0.0,
    line_jitter_mm=0.0,
    start_blob=0.0,
    height_shading=0.0,
)
"""Ideal print: on a screen the offsets must be the only displacement."""


@dataclass
class ScreenImage:
    image: np.ndarray
    """BGR uint8, exactly the screen resolution."""
    px_per_mm: float
    """Screen pixels per (pattern or board) millimetre."""
    scale: float
    """Displayed size divided by the real size (1 = actual size)."""


def _fit_scale(content_mm: tuple[float, float], size_px: tuple[int, int], ppi: float, fill: float):
    """Screen px per mm: actual size if it fits, otherwise the largest size that does."""
    native = ppi / 25.4
    fit = fill * min(size_px[0] / content_mm[0], size_px[1] / content_mm[1])
    return min(native, fit), native


def _landscape(size_px: tuple[int, int]) -> tuple[tuple[int, int], bool]:
    w, h = size_px
    return ((h, w), True) if h > w else ((w, h), False)


def _paste_centred(canvas: np.ndarray, img: np.ndarray) -> np.ndarray:
    H, W = canvas.shape[:2]
    h, w = img.shape[:2]
    r0, c0 = (H - h) // 2, (W - w) // 2
    canvas[r0 : r0 + h, c0 : c0 + w] = img
    return canvas


def round_offsets(
    offsets_mm: dict[int, tuple[float, float]], px_per_mm: float
) -> dict[int, tuple[float, float]]:
    """Snap offsets to whole screen pixels, so that every tool's elements are exact
    pixel-shifted copies and the truth does not depend on anti-aliasing."""
    return {
        t: (round(dx * px_per_mm) / px_per_mm, round(dy * px_per_mm) / px_per_mm)
        for t, (dx, dy) in offsets_mm.items()
    }


def pattern_on_screen(
    layout: Layout,
    offsets_mm: dict[int, tuple[float, float]],
    size_px: tuple[int, int],
    ppi: float,
    mono: bool = False,
) -> tuple[ScreenImage, dict[int, tuple[float, float]]]:
    """Full-screen image of the pattern with the given tool offsets.

    Returns the image and the offsets actually displayed (snapped to whole pixels).
    A portrait ``size_px`` gets a landscape pattern rotated by 90 deg. ``mono`` draws
    every tool in the reference colour: a colour screen lights each colour with its
    own subpixels, a fraction of a pixel apart, which can bias coloured lines
    against white ones; with one colour that effect cancels.
    """
    (w, h), portrait = _landscape(size_px)
    x0, y0, x1, y1 = layout.bbox()
    m = SCREEN_MARGIN_MM
    s, native = _fit_scale((x1 - x0 + 2 * m, y1 - y0 + 2 * m), (w, h), ppi, fill=1.0)
    shown = round_offsets(offsets_mm, s)
    app = CLEAN
    if mono:
        app = CLEAN.model_copy(
            update={"tool_rgb": dict.fromkeys(CLEAN.tool_rgb, CLEAN.tool_rgb[0])}
        )
    tex = render_texture(layout, shown, app, np.random.default_rng(0), s, margin_mm=m)
    rgb = np.clip(tex.rgb, 0.0, 1.0)
    canvas = np.empty((h, w, 3), np.float32)
    canvas[:] = np.asarray(CLEAN.bed_rgb, np.float32)
    canvas = _paste_centred(canvas, rgb[: min(h, rgb.shape[0]), : min(w, rgb.shape[1])])
    bgr = (canvas[..., ::-1] * 255.0 + 0.5).astype(np.uint8)
    if portrait:
        bgr = np.ascontiguousarray(np.rot90(bgr))
    return ScreenImage(bgr, s, s / native), shown


def board_on_screen(cfg: CharucoBoardConfig, size_px: tuple[int, int], ppi: float) -> ScreenImage:
    """Full-screen ChArUco board with a white border. Its scale does not affect the
    lens intrinsics, so it is shrunk to fit when it is larger than the screen."""
    (w, h), portrait = _landscape(size_px)
    margin = cfg.square_mm / 2
    content = (
        cfg.squares_x * cfg.square_mm + 2 * margin,
        cfg.squares_y * cfg.square_mm + 2 * margin,
    )
    s, native = _fit_scale(content, (w, h), ppi, fill=0.98)
    board = rasterize(cfg, s, margin_mm=margin)
    canvas = _paste_centred(np.ones((h, w), np.float32), board)
    gray = (canvas * 255.0 + 0.5).astype(np.uint8)
    bgr = np.repeat(gray[..., None], 3, axis=2)
    if portrait:
        bgr = np.ascontiguousarray(np.rot90(bgr))
    return ScreenImage(bgr, s, s / native)
