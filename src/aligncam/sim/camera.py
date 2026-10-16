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

    def max_gsd_at(self, xy: tuple[float, float]) -> float:
        """Coarser of the X and Y ground sampling distances around a bed point."""
        h = 1.0
        p = self.project(np.array([xy, (xy[0] + h, xy[1]), (xy[0], xy[1] + h)]))
        return float(h / min(np.hypot(*(p[1] - p[0])), np.hypot(*(p[2] - p[0]))))

    def view_angle_deg(self, xy: tuple[float, float]) -> float:
        """Angle between the line of sight to a bed point and the vertical."""
        ray = np.array([xy[0], xy[1], 0.0]) + self.R.T @ self.t
        return float(np.degrees(np.arccos(-ray[2] / np.linalg.norm(ray))))


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
    distance_mm: float = 250.0,
    target: tuple[float, float] = (0.0, 0.0),
    margin_px: int = 24,
    k1: float = 0.0,
) -> PinholeCamera:
    """Camera whose image is cropped around ``roi_mm`` (pattern frame).

    The camera sits at ``distance_mm`` from ``target`` and its focal length is chosen
    to give the requested GSD on the optical axis (a finer GSD means a sharper
    sensor or a longer lens, not a closer camera). ``tilt_deg`` is measured from the vertical,
    ``azimuth_deg`` is the direction of the camera as seen from the target
    (-90 = in front of the bed), ``roll_deg`` rotates the image. Cropping the image
    to the region of interest is equivalent to a larger sensor with a shifted
    principal point, and keeps the benchmark fast.
    """
    d = distance_mm
    focal_px = distance_mm / gsd_mm_per_px
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
    return _cropped_camera(focal_px, R, pos, roi_mm, margin_px, k1)


def camera_from_pose(
    position_mm: tuple[float, float, float],
    aim_mm: tuple[float, float],
    hfov_deg: float,
    roi_mm: tuple[float, float, float, float],
    width_px: int = 1920,
    height_px: int = 1080,
    roll_deg: float = 0.0,
    margin_px: int = 24,
    k1: float = 0.0,
) -> PinholeCamera:
    """Physical camera at ``position_mm`` (pattern frame, z above the bed) aimed at
    ``aim_mm`` on the bed, with a ``width_px`` x ``height_px`` sensor and horizontal
    FOV ``hfov_deg``.

    The image is cropped around ``roi_mm`` as in :func:`camera_for_gsd`; the
    principal point and the distortion centre stay on the true optical axis, so a
    pattern far from the image centre sees the distortion of the image edge.
    """
    pos = np.asarray(position_mm, dtype=float)
    tgt = np.array([aim_mm[0], aim_mm[1], 0.0])
    view = tgt - pos
    # Image up = world up (horizon level), unless the camera looks straight down.
    horizontal = np.hypot(view[0], view[1]) > 1e-6 * np.linalg.norm(view)
    up = np.array([0.0, 0.0, 1.0]) if horizontal else np.array([0.0, 1.0, 0.0])
    R = look_at(pos, tgt, up)
    roll = math.radians(roll_deg)
    rz = np.array(
        [[math.cos(roll), -math.sin(roll), 0], [math.sin(roll), math.cos(roll), 0], [0, 0, 1]]
    )
    focal_px = (width_px / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    return _cropped_camera(
        focal_px, rz @ R, pos, roi_mm, margin_px, k1, sensor_px=(width_px, height_px)
    )


def _cropped_camera(
    focal_px: float,
    R: np.ndarray,
    pos: np.ndarray,
    roi_mm: tuple[float, float, float, float],
    margin_px: int,
    k1: float,
    sensor_px: tuple[int, int] | None = None,
) -> PinholeCamera:
    """Pinhole camera cropped to ``roi_mm``. With ``sensor_px`` (principal point at the
    sensor centre) the crop is also clipped to the sensor, so whatever falls outside
    the real frame is not rendered (clipping uses undistorted coordinates)."""
    t = -R @ pos
    K = np.array([[focal_px, 0, 0], [0, focal_px, 0], [0, 0, 1.0]])
    probe = PinholeCamera(K, R, t, (1, 1))
    x0, y0, x1, y1 = roi_mm
    corners = probe.project(np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]))
    lo = corners.min(axis=0) - margin_px
    hi = corners.max(axis=0) + margin_px
    if sensor_px is not None:
        half = np.array(sensor_px, dtype=float) / 2.0
        lo = np.clip(lo, -half, half - 8)
        hi = np.clip(hi, lo + 8, half)
    K[0, 2], K[1, 2] = -lo[0], -lo[1]
    size = (int(math.ceil(hi[0] - lo[0])), int(math.ceil(hi[1] - lo[1])))
    # OpenCV radial model on normalised coordinates (x / f), centred on the optical axis.
    dist = None if k1 == 0 else np.array([k1, 0.0, 0.0, 0.0, 0.0])
    return PinholeCamera(K, R, t, size, dist)
