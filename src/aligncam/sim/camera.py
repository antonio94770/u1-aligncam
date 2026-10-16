"""Pinhole camera model for the synthetic scenes.

World frame = pattern frame (mm), with the bed in the plane z = 0 and +Z up.
Camera frame = OpenCV convention (x right, y down, z forward).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class PinholeCamera:
    K: np.ndarray
    R: np.ndarray
    """World-to-camera rotation."""
    t: np.ndarray
    size: tuple[int, int]
    """Image size (width, height) in pixels."""
    dist: np.ndarray | None = None
    """OpenCV distortion coefficients (k1, k2, p1, p2, k3) or None."""

    def homography(self) -> np.ndarray:
        """Undistorted image pixels from bed-plane mm: (X, Y, 1) -> (u, v, 1)."""
        H = self.K @ np.column_stack([self.R[:, 0], self.R[:, 1], self.t])
        return H / H[2, 2]

    def project(self, xy: np.ndarray) -> np.ndarray:
        """Project bed-plane points (N, 2) to distorted image pixels (N, 2)."""
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        obj = np.column_stack([xy, np.zeros(len(xy))])
        rvec, _ = cv2.Rodrigues(self.R)
        dist = np.zeros(5) if self.dist is None else self.dist
        pts, _ = cv2.projectPoints(obj, rvec, self.t, self.K, dist)
        return pts.reshape(-1, 2)

    def gsd_at(self, xy: tuple[float, float]) -> float:
        """Mean ground sampling distance (mm per pixel) around a bed point."""
        h = 1.0
        p = self.project(np.array([xy, (xy[0] + h, xy[1]), (xy[0], xy[1] + h)]))
        sx = np.hypot(*(p[1] - p[0]))
        sy = np.hypot(*(p[2] - p[0]))
        return float(2.0 * h / (sx + sy))


def look_at(position: np.ndarray, target: np.ndarray, image_up_world: np.ndarray) -> np.ndarray:
    """World-to-camera rotation for a camera at ``position`` looking at ``target``.

    ``image_up_world`` is the world direction that should point up in the image.
    """
    z = np.asarray(target, float) - np.asarray(position, float)
    z /= np.linalg.norm(z)
    u = np.asarray(image_up_world, float)
    u = u - (u @ z) * z
    if np.linalg.norm(u) < 1e-9:
        raise ValueError("image_up_world is parallel to the viewing direction")
    y = -u / np.linalg.norm(u)
    x = np.cross(y, z)
    return np.vstack([x, y, z])


def gsd_for_camera(width_px: int, hfov_deg: float, distance_mm: float) -> float:
    """Ground sampling distance of a camera looking straight down from ``distance_mm``."""
    return distance_mm * 2.0 * math.tan(math.radians(hfov_deg) / 2.0) / width_px


def distance_for_gsd(width_px: int, hfov_deg: float, gsd_mm_per_px: float) -> float:
    return gsd_mm_per_px * width_px / (2.0 * math.tan(math.radians(hfov_deg) / 2.0))


def camera_for_gsd(
    gsd_mm_per_px: float,
    roi_mm: tuple[float, float, float, float],
    tilt_deg: float = 0.0,
    azimuth_deg: float = -90.0,
    roll_deg: float = 0.0,
    focal_px: float = 1400.0,
    target: tuple[float, float] = (0.0, 0.0),
    margin_px: int = 24,
    k1: float = 0.0,
) -> PinholeCamera:
    """Camera whose image is cropped around ``roi_mm`` (pattern frame).

    The camera sits on a sphere around ``target`` at the distance that gives the
    requested GSD on the optical axis. ``tilt_deg`` is measured from the vertical,
    ``azimuth_deg`` is the direction of the camera as seen from the target
    (-90 = in front of the bed), ``roll_deg`` rotates the image. Cropping the image
    to the region of interest is equivalent to a larger sensor with a shifted
    principal point, and keeps the benchmark fast.
    """
    d = gsd_mm_per_px * focal_px
    tilt, az = math.radians(tilt_deg), math.radians(azimuth_deg)
    tgt = np.array([target[0], target[1], 0.0])
    pos = tgt + d * np.array(
        [math.sin(tilt) * math.cos(az), math.sin(tilt) * math.sin(az), math.cos(tilt)]
    )
    # "Image up" = away from the camera horizontally, or +Y for a top-down view.
    up = np.array([-math.cos(az), -math.sin(az), 0.0]) if tilt_deg > 1e-6 else np.array([0, 1, 0.0])
    roll = math.radians(roll_deg)
    rz = np.array(
        [[math.cos(roll), -math.sin(roll), 0], [math.sin(roll), math.cos(roll), 0], [0, 0, 1]]
    )
    up = rz @ up
    R = look_at(pos, tgt, up)
    t = -R @ pos
    K = np.array([[focal_px, 0, 0], [0, focal_px, 0], [0, 0, 1.0]])
    probe = PinholeCamera(K, R, t, (1, 1))
    x0, y0, x1, y1 = roi_mm
    corners = probe.project(np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]))
    lo = corners.min(axis=0) - margin_px
    hi = corners.max(axis=0) + margin_px
    K[0, 2], K[1, 2] = -lo[0], -lo[1]
    size = (int(math.ceil(hi[0] - lo[0])), int(math.ceil(hi[1] - lo[1])))
    dist = None if k1 == 0 else np.array([k1, 0.0, 0.0, 0.0, 0.0])
    if dist is not None:
        # Distortion is centred on the crop, so express it on the crop half-diagonal.
        r = 0.5 * math.hypot(*size) / focal_px
        dist = dist / (r * r)
    return PinholeCamera(K, R, t, size, dist)
