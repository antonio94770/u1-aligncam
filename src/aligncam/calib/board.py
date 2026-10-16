"""ChArUco calibration board: OpenCV object, printable SVG and raster texture.

Board frame: millimetres, origin at the top-left corner of the squares, +x right,
+y down the printed page, z = 0 (the OpenCV object-point convention). The SVG and the
simulator texture are both drawn from :func:`black_rects_mm`, so the printed board and
the synthetic one cannot drift apart from what the detector expects.
"""

from __future__ import annotations

import cv2
import numpy as np

from aligncam.config import CharucoBoardConfig


def make_board(cfg: CharucoBoardConfig) -> cv2.aruco.CharucoBoard:
    name = cfg.dictionary
    if not name.startswith("DICT_") or not hasattr(cv2.aruco, name):
        raise ValueError(f"unknown ArUco dictionary {name!r}")
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))
    n_markers = (cfg.squares_x * cfg.squares_y) // 2
    if n_markers > dictionary.bytesList.shape[0]:
        raise ValueError(f"{name} has fewer than the {n_markers} markers the board needs")
    return cv2.aruco.CharucoBoard(
        (cfg.squares_x, cfg.squares_y), cfg.square_mm, cfg.marker_mm, dictionary
    )


def black_rects_mm(cfg: CharucoBoardConfig) -> list[tuple[float, float, float, float]]:
    """Every black area of the board as (x, y, width, height) in the board frame.

    Squares that hold a marker are white; the others are black. Marker cells are merged
    into horizontal runs to keep the SVG small.
    """
    board = make_board(cfg)
    dictionary = board.getDictionary()
    bits = dictionary.markerSize + 2  # one black border cell on each side
    cell = cfg.marker_mm / bits
    sq = cfg.square_mm
    rects: list[tuple[float, float, float, float]] = []
    holds_marker = np.zeros((cfg.squares_y, cfg.squares_x), dtype=bool)
    for marker_id, corners in zip(board.getIds().ravel(), board.getObjPoints(), strict=True):
        x0, y0 = (float(v) for v in np.asarray(corners).reshape(-1, 3)[0, :2])
        holds_marker[int(y0 // sq), int(x0 // sq)] = True
        img = cv2.aruco.generateImageMarker(dictionary, int(marker_id), bits, borderBits=1)
        for r in range(bits):
            c = 0
            while c < bits:
                if img[r, c] >= 128:
                    c += 1
                    continue
                start = c
                while c < bits and img[r, c] < 128:
                    c += 1
                rects.append((x0 + start * cell, y0 + r * cell, (c - start) * cell, cell))
    for j in range(cfg.squares_y):
        for i in range(cfg.squares_x):
            if not holds_marker[j, i]:
                rects.append((i * sq, j * sq, sq, sq))
    return rects


def board_svg(cfg: CharucoBoardConfig, margin_mm: float = 10.0) -> str:
    """Printable SVG with physical (mm) size. Print at 100 % on matte paper."""
    w = cfg.squares_x * cfg.square_mm
    h = cfg.squares_y * cfg.square_mm
    page_w, page_h = w + 2 * margin_mm, h + 2 * margin_mm + 6.0
    label = (
        f"aligncam ChArUco {cfg.squares_x}x{cfg.squares_y}, square {cfg.square_mm:g} mm, "
        f"marker {cfg.marker_mm:g} mm, {cfg.dictionary}. Keep it flat; print scale does "
        "not matter."
    )
    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{page_w:g}mm" height="{page_h:g}mm" viewBox="0 0 {page_w:g} {page_h:g}">',
        f'<rect width="{page_w:g}" height="{page_h:g}" fill="#ffffff"/>',
        f'<g transform="translate({margin_mm:g} {margin_mm:g})" fill="#000000" '
        'shape-rendering="crispEdges">',
    ]
    out += [
        f'<rect x="{x:.4f}" y="{y:.4f}" width="{rw:.4f}" height="{rh:.4f}"/>'
        for x, y, rw, rh in black_rects_mm(cfg)
    ]
    out += [
        "</g>",
        f'<text x="{margin_mm:g}" y="{page_h - 4:g}" font-family="sans-serif" '
        f'font-size="3" fill="#000000">{label}</text>',
        "</svg>",
        "",
    ]
    return "\n".join(out)


def rasterize(cfg: CharucoBoardConfig, px_per_mm: float, margin_mm: float = 0.0) -> np.ndarray:
    """Board as a float32 image (1 = white, 0 = black), anti-aliased by 4x supersampling.

    Pixel ``i`` covers ``[i, i + 1) / px_per_mm`` mm, measured from ``-margin_mm``.
    """
    ss = 4
    s = px_per_mm * ss
    w = int(round((cfg.squares_x * cfg.square_mm + 2 * margin_mm) * px_per_mm))
    h = int(round((cfg.squares_y * cfg.square_mm + 2 * margin_mm) * px_per_mm))
    canvas = np.ones((h * ss, w * ss), dtype=np.float32)
    for x, y, rw, rh in black_rects_mm(cfg):
        c0 = int(round((x + margin_mm) * s))
        r0 = int(round((y + margin_mm) * s))
        c1 = int(round((x + rw + margin_mm) * s))
        r1 = int(round((y + rh + margin_mm) * s))
        canvas[r0:r1, c0:c1] = 0.0
    return cv2.resize(canvas, (w, h), interpolation=cv2.INTER_AREA)
