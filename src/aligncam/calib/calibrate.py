"""Lens calibration from photos of a ChArUco board, with quality gates.

ChArUco rather than a plain checkerboard: every corner is identified by the ArUco
markers around it, so a view where the board is only partly inside the frame still
contributes its visible corners. The image corners and edges, where a wide-angle lens
distorts most, can then be covered without fitting the whole board in view, which
matters for a fixed chamber camera. A plain checkerboard is rejected by OpenCV as
soon as one inner corner is hidden.

Like the measurement, the calibration refuses rather than guesses: the result has a
status and explicit reasons when a quality gate fails.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import cv2
import numpy as np

from aligncam.calib.board import make_board
from aligncam.config import CalibrationConfig

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")

_MODEL_FLAGS = {
    "k1": cv2.CALIB_FIX_K2 | cv2.CALIB_FIX_K3 | cv2.CALIB_ZERO_TANGENT_DIST,
    "k1k2": cv2.CALIB_FIX_K3 | cv2.CALIB_ZERO_TANGENT_DIST,
    "k1k2p": cv2.CALIB_FIX_K3,
    "full": 0,
}


class CalibReason(StrEnum):
    TOO_FEW_VIEWS = "too_few_views"
    """Not enough photos with a usable number of detected corners."""
    HIGH_REPROJECTION = "high_reprojection"
    """The fitted model does not explain the detected corners (blur, a board that is
    not flat, or a lens the distortion model cannot describe)."""
    POOR_COVERAGE = "poor_coverage"
    """The corners do not reach enough of the image, so the distortion is
    extrapolated where it was never observed."""
    LOW_TILT_SPREAD = "low_tilt_spread"
    """All views show the board at nearly the same orientation, so the focal length
    is poorly constrained."""
    UNSTABLE_INTRINSICS = "unstable_intrinsics"
    """OpenCV's standard deviation of the focal length or principal point is large."""


@dataclass
class ViewDetection:
    name: str
    obj_mm: np.ndarray
    """(N, 3) float32 board-frame corner positions."""
    img_px: np.ndarray
    """(N, 2) float32 detected corner positions."""


@dataclass
class CalibrationResult:
    status: str
    reasons: list[CalibReason]
    messages: list[str]
    """One human-readable explanation per reason."""
    image_size: tuple[int, int] | None
    """(width, height) in pixels."""
    camera_matrix: np.ndarray | None = None
    dist_coeffs: np.ndarray | None = None
    """(k1, k2, p1, p2, k3)."""
    rms_px: float = math.nan
    used_views: list[str] = field(default_factory=list)
    skipped_views: dict[str, str] = field(default_factory=dict)
    view_rms_px: dict[str, float] = field(default_factory=dict)
    coverage: float = 0.0
    tilt_spread_deg: float = 0.0
    focal_rel_std: float = math.nan
    principal_point_std_px: float = math.nan
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reasons": [str(r) for r in self.reasons],
            "messages": self.messages,
            "warnings": self.warnings,
            "image_size": None if self.image_size is None else list(self.image_size),
            "camera_matrix": None
            if self.camera_matrix is None
            else np.round(self.camera_matrix, 4).tolist(),
            "dist_coeffs": None
            if self.dist_coeffs is None
            else np.round(self.dist_coeffs, 6).tolist(),
            "rms_px": _r(self.rms_px, 4),
            "coverage": _r(self.coverage, 3),
            "tilt_spread_deg": _r(self.tilt_spread_deg, 1),
            "focal_rel_std": _r(self.focal_rel_std, 5),
            "principal_point_std_px": _r(self.principal_point_std_px, 2),
            "used_views": self.used_views,
            "skipped_views": self.skipped_views,
            "view_rms_px": {k: _r(v, 4) for k, v in self.view_rms_px.items()},
        }

    def to_yaml(self) -> str:
        """Snippet for the ``camera`` section (merge it with ``-c`` after the template)."""
        if self.camera_matrix is None or self.dist_coeffs is None or self.image_size is None:
            raise ValueError("no calibration to write")
        K, d = self.camera_matrix, self.dist_coeffs
        rows = ", ".join("[" + ", ".join(f"{v:.4f}" for v in row) + "]" for row in K)
        return "\n".join(
            [
                "# Lens calibration written by `aligncam calibrate-camera`.",
                f"# {len(self.used_views)} views, RMS reprojection {self.rms_px:.3f} px, "
                f"corner coverage {self.coverage * 100:.0f} %, "
                f"tilt spread {self.tilt_spread_deg:.0f} deg.",
                "# Valid only for this camera, this resolution and this focus.",
                "camera:",
                f"  resolution_px: [{self.image_size[0]}, {self.image_size[1]}]",
                f"  camera_matrix: [{rows}]",
                "  dist_coeffs: [" + ", ".join(f"{v:.6f}" for v in d) + "]  # k1, k2, p1, p2, k3",
                "",
            ]
        )


def _r(v: float, nd: int) -> float | None:
    return None if not math.isfinite(v) else round(float(v), nd)


def detect_view(
    name: str, img: np.ndarray, board: cv2.aruco.CharucoBoard, min_corners: int
) -> ViewDetection | str:
    """Detected corners of one photo, or the reason the photo is skipped."""
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    corners, ids, _, _ = cv2.aruco.CharucoDetector(board).detectBoard(gray)
    n = 0 if ids is None else len(ids)
    if n < min_corners:
        return f"{n} corners detected (minimum {min_corners})"
    obj, pts = board.matchImagePoints(corners, ids)
    return ViewDetection(
        name, obj.reshape(-1, 3).astype(np.float32), pts.reshape(-1, 2).astype(np.float32)
    )


def calibrate_images(
    images: Iterable[tuple[str, np.ndarray]], cfg: CalibrationConfig | None = None
) -> CalibrationResult:
    """Detect the board in every image and calibrate. All images must have one size."""
    cfg = cfg or CalibrationConfig()
    board = make_board(cfg.board)
    views: list[ViewDetection] = []
    skipped: dict[str, str] = {}
    size: tuple[int, int] | None = None
    for name, img in images:
        h, w = img.shape[:2]
        if size is None:
            size = (w, h)
        elif (w, h) != size:
            raise ValueError(f"{name} is {w} x {h}, the first image is {size[0]} x {size[1]}")
        det = detect_view(name, img, board, cfg.min_corners_per_view)
        if isinstance(det, str):
            skipped[name] = det
        else:
            views.append(det)
    res = calibrate_views(views, size, cfg)
    res.skipped_views = {**skipped, **res.skipped_views}
    return res


def calibrate_folder(folder: Path, cfg: CalibrationConfig | None = None) -> CalibrationResult:
    paths = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    if not paths:
        raise ValueError(f"no images ({', '.join(IMAGE_SUFFIXES)}) in {folder}")

    def read():
        for p in paths:
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError(f"cannot read {p}")
            yield p.name, img

    return calibrate_images(read(), cfg)


def _run(views: list[ViewDetection], size: tuple[int, int], flags: int):
    out = cv2.calibrateCameraExtended(
        [v.obj_mm for v in views],
        [v.img_px.reshape(-1, 1, 2) for v in views],
        size,
        None,
        None,
        flags=flags,
    )
    rms, K, dist, rvecs, _, std_intr, _, per_view = out
    return float(rms), K, dist.ravel(), rvecs, std_intr.ravel(), per_view.ravel()


def calibrate_views(
    views: list[ViewDetection], size: tuple[int, int] | None, cfg: CalibrationConfig
) -> CalibrationResult:
    res = CalibrationResult("rejected", [], [], size)
    if size is None or len(views) < max(3, cfg.min_views):
        res.reasons.append(CalibReason.TOO_FEW_VIEWS)
        res.messages.append(
            f"{len(views)} usable views, at least {cfg.min_views} are needed: take more "
            "photos with the board at different positions and tilts"
        )
        if size is None or len(views) < 3:
            res.used_views = [v.name for v in views]
            return res

    flags = _MODEL_FLAGS[cfg.distortion_model]
    rms, K, dist, rvecs, std, per_view = _run(views, size, flags)
    # Drop gross outlier views once (a misdetection, a bent board in one photo).
    limit = max(cfg.outlier_view_factor * float(np.median(per_view)), cfg.max_rms_px)
    bad = [i for i, e in enumerate(per_view) if e > limit]
    if bad and len(views) - len(bad) >= max(3, cfg.min_views):
        for i in bad:
            res.skipped_views[views[i].name] = (
                f"outlier: view RMS {per_view[i]:.2f} px > {limit:.2f} px"
            )
        res.warnings.append(f"dropped {len(bad)} outlier view(s) and recalibrated")
        views = [v for i, v in enumerate(views) if i not in bad]
        rms, K, dist, rvecs, std, per_view = _run(views, size, flags)

    d = np.zeros(5)
    d[: min(5, len(dist))] = dist[:5]
    res.camera_matrix, res.dist_coeffs, res.rms_px = K, d, rms
    res.used_views = [v.name for v in views]
    res.view_rms_px = {v.name: float(e) for v, e in zip(views, per_view, strict=True)}
    res.coverage, missing = _coverage(views, size, cfg.coverage_grid)
    res.tilt_spread_deg = _tilt_spread_deg(rvecs)
    res.focal_rel_std = float(max(std[0] / K[0, 0], std[1] / K[1, 1]))
    res.principal_point_std_px = float(max(std[2], std[3]))

    if rms > cfg.max_rms_px:
        res.reasons.append(CalibReason.HIGH_REPROJECTION)
        res.messages.append(
            f"RMS reprojection error {rms:.2f} px > {cfg.max_rms_px:.2f} px: check focus, "
            "motion blur and that the board is flat"
        )
    if res.coverage < cfg.min_coverage:
        res.reasons.append(CalibReason.POOR_COVERAGE)
        hint = f"; no corners near the {', '.join(missing)} corner(s)" if missing else ""
        res.messages.append(
            f"corners cover {res.coverage * 100:.0f} % of the image "
            f"(minimum {cfg.min_coverage * 100:.0f} %){hint}: move the board towards "
            "the image edges and corners"
        )
    if res.tilt_spread_deg < cfg.min_tilt_spread_deg:
        res.reasons.append(CalibReason.LOW_TILT_SPREAD)
        res.messages.append(
            f"board orientations differ by at most {res.tilt_spread_deg:.0f} deg "
            f"(minimum {cfg.min_tilt_spread_deg:.0f} deg): tilt the board 20-45 deg in "
            "different directions, do not keep it flat on the bed"
        )
    if (
        res.focal_rel_std > cfg.max_focal_rel_std
        or res.principal_point_std_px > cfg.max_principal_point_std_px
    ):
        res.reasons.append(CalibReason.UNSTABLE_INTRINSICS)
        res.messages.append(
            f"focal length std {res.focal_rel_std * 100:.2f} % (max "
            f"{cfg.max_focal_rel_std * 100:.2f} %), principal point std "
            f"{res.principal_point_std_px:.1f} px (max {cfg.max_principal_point_std_px:.1f} "
            "px): add more views with varied tilt"
        )
    res.status = "ok" if not res.reasons else "rejected"
    return res


_CORNER_NAMES = {
    (0, 0): "top-left",
    (1, 0): "top-right",
    (0, 1): "bottom-left",
    (1, 1): "bottom-right",
}


def _coverage(
    views: list[ViewDetection], size: tuple[int, int], grid: tuple[int, int]
) -> tuple[float, list[str]]:
    """Fraction of grid cells with at least one corner, and the empty image corners."""
    nx, ny = grid
    pts = np.concatenate([v.img_px for v in views])
    cx = np.clip((pts[:, 0] / size[0] * nx).astype(int), 0, nx - 1)
    cy = np.clip((pts[:, 1] / size[1] * ny).astype(int), 0, ny - 1)
    occupied = np.zeros((ny, nx), dtype=bool)
    occupied[cy, cx] = True
    missing = [
        name for (i, j), name in _CORNER_NAMES.items() if not occupied[j * (ny - 1), i * (nx - 1)]
    ]
    return float(occupied.mean()), missing


def _tilt_spread_deg(rvecs) -> float:
    normals = np.array([cv2.Rodrigues(np.asarray(r))[0][:, 2] for r in rvecs])
    cos = np.clip(normals @ normals.T, -1.0, 1.0)
    return float(np.degrees(np.arccos(cos.min())))
