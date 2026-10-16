"""End-to-end measurement: image -> per-tool XY offsets with uncertainty."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import cv2
import numpy as np

from aligncam.config import CameraConfig, MeasureConfig
from aligncam.geometry import Cell, Layout
from aligncam.measure.fiducials import FiducialFit, find_fiducials
from aligncam.measure.grating import GratingReading, read_grating
from aligncam.measure.quality import Reason

Status = Literal["ok", "rejected"]


@dataclass
class CellResult:
    name: str
    tool: int
    axis: str
    is_control: bool
    status: Status
    reasons: list[Reason] = field(default_factory=list)
    offset_mm: float | None = None
    """Phase estimate: measured grating shift minus reference grating shift."""
    sigma_mm: float | None = None
    vernier_offset_mm: float | None = None
    """Independent line-by-line vernier reading (cross-check)."""
    coincidence_index: float | None = None
    """Vernier reading as a human would do it: which line pair coincides."""
    reference: GratingReading | None = None
    measured: GratingReading | None = None


@dataclass
class ToolOffset:
    tool: int
    status: Status
    reasons: list[Reason] = field(default_factory=list)
    dx_mm: float | None = None
    dy_mm: float | None = None
    sigma_x_mm: float | None = None
    sigma_y_mm: float | None = None

    def correction(self, sign: int) -> tuple[float, float] | None:
        """Proposed change of the firmware offset (sign convention is TO VERIFY ON HARDWARE)."""
        if self.status != "ok" or self.dx_mm is None or self.dy_mm is None:
            return None
        return (sign * self.dx_mm, sign * self.dy_mm)


@dataclass
class Measurement:
    status: Literal["ok", "partial", "rejected"]
    reasons: list[Reason]
    warnings: list[str]
    fiducials: FiducialFit | None
    cells: list[CellResult]
    tools: dict[int, ToolOffset]

    def to_dict(self) -> dict:
        def r(v: float | None, nd: int = 4):
            return None if v is None else round(float(v), nd)

        return {
            "status": self.status,
            "reasons": [str(x) for x in self.reasons],
            "warnings": self.warnings,
            "fiducials_px": None
            if self.fiducials is None
            else np.round(self.fiducials.image_points, 2).tolist(),
            "fiducial_reproj_rms_px": None
            if self.fiducials is None
            else r(self.fiducials.reproj_rms_px, 3),
            "tools": {
                f"T{t.tool}": {
                    "status": t.status,
                    "reasons": [str(x) for x in t.reasons],
                    "dx_mm": r(t.dx_mm),
                    "dy_mm": r(t.dy_mm),
                    "sigma_x_mm": r(t.sigma_x_mm),
                    "sigma_y_mm": r(t.sigma_y_mm),
                }
                for t in self.tools.values()
            },
            "cells": [
                {
                    "name": c.name,
                    "status": c.status,
                    "control": c.is_control,
                    "reasons": [str(x) for x in c.reasons],
                    "offset_mm": r(c.offset_mm),
                    "sigma_mm": r(c.sigma_mm),
                    "vernier_offset_mm": r(c.vernier_offset_mm),
                    "coincidence_index": r(c.coincidence_index, 2),
                    "snr": None
                    if c.measured is None or c.reference is None
                    else r(min(c.measured.snr, c.reference.snr), 2),
                }
                for c in self.cells
            ],
        }


def _cell_inside(cell: Cell, H: np.ndarray, size: tuple[int, int], lw: float, pad: float) -> bool:
    x0, y0, x1, y1 = cell.bbox(lw)
    pts = np.array(
        [[x0 - pad, y0 - pad], [x1 + pad, y0 - pad], [x1 + pad, y1 + pad], [x0 - pad, y1 + pad]],
        dtype=np.float64,
    )
    q = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), H).reshape(-1, 2)
    w, h = size
    return bool(
        np.all(q[:, 0] >= 0)
        and np.all(q[:, 0] < w - 1)
        and np.all(q[:, 1] >= 0)
        and np.all(q[:, 1] < h - 1)
    )


def _vernier_reading(cell: Cell, ref: GratingReading, meas: GratingReading):
    """Fit d_k = u_meas(k) - u_ref(k) = delta + k * (p_meas - p_ref) over the line pairs."""
    ref_map = dict(zip(ref.line_indices.tolist(), ref.line_centers_u, strict=True))
    ks, ds = [], []
    for k, um in zip(meas.line_indices.tolist(), meas.line_centers_u, strict=True):
        ur = ref_map.get(k)
        if ur is None or math.isnan(ur) or math.isnan(um):
            continue
        ks.append(k)
        ds.append(um - ur)
    if len(ks) < 5:
        return None, None
    ks_arr, ds_arr = np.array(ks, float), np.array(ds, float)
    slope_nominal = cell.measured.pitch - cell.reference.pitch
    # Robust: fix the slope to its nominal value and take the median intercept.
    intercept = float(np.median(ds_arr - slope_nominal * ks_arr))
    coincidence = None if abs(slope_nominal) < 1e-9 else -intercept / slope_nominal
    return intercept, coincidence


def measure_cell(
    img_rgb: np.ndarray, H: np.ndarray, cell: Cell, layout: Layout, cfg: MeasureConfig
) -> CellResult:
    res = CellResult(cell.name, cell.tool, cell.axis, cell.is_control, "rejected")
    search = cfg.max_offset_mm + 1.0
    if not _cell_inside(
        cell, H, (img_rgb.shape[1], img_rgb.shape[0]), layout.line_width, search + 1.0
    ):
        res.reasons.append(Reason.OUTSIDE_IMAGE)
        return res
    ref = read_grating(img_rgb, H, cell.reference, cfg, search)
    meas = read_grating(img_rgb, H, cell.measured, cfg, search)
    res.reference, res.measured = ref, meas
    res.offset_mm = meas.shift_mm - ref.shift_mm
    res.sigma_mm = math.sqrt(meas.sigma_mm**2 + ref.sigma_mm**2 + cfg.sigma_floor_mm**2)
    res.vernier_offset_mm, res.coincidence_index = _vernier_reading(cell, ref, meas)

    if min(ref.contrast, meas.contrast) < cfg.min_contrast:
        res.reasons.append(Reason.LOW_CONTRAST)
    if min(ref.snr, meas.snr) < cfg.min_modulation_snr:
        res.reasons.append(Reason.LOW_SNR)
    if abs(res.offset_mm) > cfg.max_offset_mm:
        res.reasons.append(Reason.OUT_OF_RANGE)
    if res.vernier_offset_mm is None or (
        abs(res.offset_mm - res.vernier_offset_mm) > cfg.max_phase_vernier_disagreement_mm
    ):
        res.reasons.append(Reason.PHASE_VERNIER_MISMATCH)
    if not math.isfinite(res.sigma_mm) or res.sigma_mm > cfg.max_sigma_mm:
        res.reasons.append(Reason.HIGH_UNCERTAINTY)
    res.status = "ok" if not res.reasons else "rejected"
    return res


def measure_image(
    img_bgr: np.ndarray,
    layout: Layout,
    cfg: MeasureConfig | None = None,
    camera: CameraConfig | None = None,
) -> Measurement:
    cfg = cfg or MeasureConfig()
    if camera is not None and camera.camera_matrix is not None:
        K = np.asarray(camera.camera_matrix, float)
        dist = np.asarray(camera.dist_coeffs or [0, 0, 0, 0, 0], float)
        img_bgr = cv2.undistort(img_bgr, K, dist)

    tools = list(layout.config.tools)
    fid = find_fiducials(img_bgr, layout)
    if fid is None or fid.reproj_rms_px > cfg.max_fiducial_reproj_px:
        reason = Reason.NO_FIDUCIALS if fid is None else Reason.FIDUCIAL_REPROJECTION
        return Measurement(
            "rejected",
            [reason],
            [],
            fid,
            [],
            {t: ToolOffset(t, "rejected", [reason]) for t in tools},
        )

    img_rgb = (img_bgr[..., ::-1].astype(np.float32) / 255.0).copy()
    cells = [measure_cell(img_rgb, fid.H_img_from_mm, c, layout, cfg) for c in layout.cells]

    reasons: list[Reason] = []
    warnings: list[str] = []
    control_failed = False
    for c in cells:
        if not c.is_control:
            continue
        if c.status != "ok":
            warnings.append(f"control cell {c.name} unreadable: {', '.join(c.reasons)}")
        elif abs(c.offset_mm) > cfg.max_control_bias_mm:
            control_failed = True
            warnings.append(f"control cell {c.name} reads {c.offset_mm:+.3f} mm instead of 0")
    if control_failed:
        reasons.append(Reason.CONTROL_BIAS)

    by_key = {(c.tool, c.axis): c for c in cells}
    out: dict[int, ToolOffset] = {}
    for t in tools:
        cx, cy = by_key.get((t, "x")), by_key.get((t, "y"))
        to = ToolOffset(t, "rejected")
        for axis_cell in (cx, cy):
            if axis_cell is None:
                to.reasons.append(Reason.MISSING_CELL)
            else:
                to.reasons += [r for r in axis_cell.reasons if r not in to.reasons]
        if cx is not None:
            to.dx_mm, to.sigma_x_mm = cx.offset_mm, cx.sigma_mm
        if cy is not None:
            to.dy_mm, to.sigma_y_mm = cy.offset_mm, cy.sigma_mm
        if control_failed:
            to.reasons.append(Reason.CONTROL_BIAS)
        to.status = "ok" if not to.reasons else "rejected"
        out[t] = to

    n_ok = sum(1 for t in out.values() if t.status == "ok")
    status = "ok" if n_ok == len(out) else ("partial" if n_ok else "rejected")
    return Measurement(status, reasons, warnings, fid, cells, out)
