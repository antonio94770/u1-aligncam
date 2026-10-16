"""Synthetic photos of the ChArUco calibration board, for tests and the benchmark.

The board is a plane in front of a full-sensor camera (no crop). Each view places it
at a different image position and tilt, the way a user would hold it in front of a
fixed chamber camera. Views near the frame edges are only partly visible, which a
ChArUco board tolerates.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from aligncam.calib.board import rasterize
from aligncam.calib.calibrate import CalibrationResult, calibrate_images
from aligncam.config import CalibrationConfig, CharucoBoardConfig
from aligncam.sim.camera import PinholeCamera
from aligncam.sim.degrade import Degradation, apply_degradation, project_texture
from aligncam.sim.render import Texture

PAPER_RGB = 0.85
INK_RGB = 0.05
BACKGROUND_RGB = (0.25, 0.25, 0.26)


def board_texture(cfg: CharucoBoardConfig, px_per_mm: float = 8.0) -> Texture:
    margin = cfg.square_mm / 2.0
    img = rasterize(cfg, px_per_mm, margin)
    rgb = np.repeat((INK_RGB + (PAPER_RGB - INK_RGB) * img)[..., None], 3, axis=2)
    # Board mm -> texture pixel; pixel centres sit at integers.
    off = margin * px_per_mm - 0.5
    tex_from_mm = np.array([[px_per_mm, 0, off], [0, px_per_mm, off], [0, 0, 1.0]])
    return Texture(rgb.astype(np.float32), tex_from_mm, px_per_mm)


def _rot(axis: np.ndarray, angle: float) -> np.ndarray:
    return cv2.Rodrigues(np.asarray(axis, float) * angle)[0]


def view_poses(
    K: np.ndarray,
    size: tuple[int, int],
    cfg: CharucoBoardConfig,
    rng: np.random.Generator,
    n_views: int = 15,
    board_fraction: float = 0.4,
    tilt_deg: tuple[float, float] = (15.0, 45.0),
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Board-to-camera (R, t) for views spread over the frame.

    Board centres follow a jittered 4 x 3 grid of image positions (then random ones),
    including the edges; the board spans about ``board_fraction`` of the image width
    and is tilted around a random in-plane axis.
    """
    w, h = size
    board_w = cfg.squares_x * cfg.square_mm
    centre_mm = np.array([board_w / 2, cfg.squares_y * cfg.square_mm / 2, 0.0])
    depth = board_w * K[0, 0] / (board_fraction * w)
    grid = [(gx, gy) for gy in (0.12, 0.5, 0.88) for gx in (0.1, 0.37, 0.63, 0.9)]
    poses = []
    for i in range(n_views):
        if i < len(grid):
            gx, gy = grid[i]
            u = (gx + rng.uniform(-0.04, 0.04)) * w
            v = (gy + rng.uniform(-0.04, 0.04)) * h
        else:
            u, v = rng.uniform(0.1, 0.9) * w, rng.uniform(0.1, 0.9) * h
        z = depth * rng.uniform(0.85, 1.2)
        centre = np.linalg.solve(K, [u, v, 1.0]) * z
        phi = rng.uniform(0, 2 * math.pi)
        tilt = _rot([math.cos(phi), math.sin(phi), 0.0], math.radians(rng.uniform(*tilt_deg)))
        R = tilt @ _rot([0, 0, 1.0], rng.uniform(-0.3, 0.3))
        poses.append((R, centre - R @ centre_mm))
    return poses


def board_views(
    K: np.ndarray,
    dist: np.ndarray | None,
    size: tuple[int, int],
    cfg: CharucoBoardConfig,
    rng: np.random.Generator,
    n_views: int = 15,
    degradation: Degradation | None = None,
    tilt_deg: tuple[float, float] = (15.0, 45.0),
) -> list[tuple[str, np.ndarray]]:
    """Rendered BGR photos of the board, named ``view_00.png`` ..."""
    deg = degradation or Degradation()
    tex = board_texture(cfg)
    bg = np.asarray(BACKGROUND_RGB, np.float32)
    out = []
    for i, (R, t) in enumerate(view_poses(K, size, cfg, rng, n_views, tilt_deg=tilt_deg)):
        cam = PinholeCamera(K, R, t, size, dist)
        linear = project_texture(tex, cam, bg, mask_invalid=True)
        out.append((f"view_{i:02d}.png", apply_degradation(linear, deg, rng)))
    return out


def calibrate_synthetic(
    K: np.ndarray,
    dist: np.ndarray | None,
    size: tuple[int, int],
    rng: np.random.Generator,
    cfg: CalibrationConfig | None = None,
    n_views: int = 15,
    degradation: Degradation | None = None,
) -> CalibrationResult:
    """Render board views through a known camera and calibrate from them."""
    cfg = cfg or CalibrationConfig()
    views = board_views(K, dist, size, cfg.board, rng, n_views, degradation)
    return calibrate_images(views, cfg)
