"""Reading one grating: rectification, line signal, phase fit and line centres.

Coordinates along the measured axis are expressed as ``u`` = position minus the
nominal grating centre (mm), so a perfectly placed grating has its lines at
``u = k * pitch``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from aligncam.config import MeasureConfig
from aligncam.geometry import Grating


@dataclass
class PhaseFit:
    shift_mm: float
    """Grating shift, wrapped to (-pitch/2, pitch/2]."""
    sigma_mm: float
    amplitude: float
    residual_std: float

    @property
    def snr(self) -> float:
        return self.amplitude / max(self.residual_std, 1e-9)


@dataclass
class GratingReading:
    tool: int
    shift_mm: float
    """Unwrapped shift of the printed grating relative to its nominal position."""
    sigma_mm: float
    snr: float
    coarse_mm: float
    band_shifts_mm: list[float]
    line_indices: np.ndarray = field(repr=False)
    line_centers_u: np.ndarray = field(repr=False)
    """Detected line centres (u coordinates, NaN where a line was not found)."""
    contrast: float = 0.0
    """Projected colour difference between lines and bed (0..1 RGB units)."""


def sample_region(
    img_rgb: np.ndarray,
    H_img_from_mm: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
) -> np.ndarray:
    """Sample the image on a regular mm grid. Returns (len(ys), len(xs), 3)."""
    X, Y = np.meshgrid(xs, ys)
    pts = np.stack([X.ravel(), Y.ravel(), np.ones(X.size)], axis=0)
    q = H_img_from_mm @ pts
    mx = (q[0] / q[2]).reshape(X.shape).astype(np.float32)
    my = (q[1] / q[2]).reshape(X.shape).astype(np.float32)
    return cv2.remap(img_rgb, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def grating_patch(
    img_rgb: np.ndarray,
    H_img_from_mm: np.ndarray,
    g: Grating,
    cfg: MeasureConfig,
    search_mm: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Rectified patch with axis 0 = across the lines, axis 1 = u (along the measured axis)."""
    step = 1.0 / cfg.rect_px_per_mm
    half_extent = (g.n_lines - 1) / 2.0 * g.pitch + g.pitch + search_mm
    u = np.arange(-half_extent, half_extent + step / 2, step)
    keep = g.line_length / 2.0 * (1.0 - 2.0 * cfg.end_trim_fraction)
    v = np.arange(-keep, keep + step / 2, step)
    cx, cy = g.center
    if g.axis == "x":
        patch = sample_region(img_rgb, H_img_from_mm, cx + u, cy + v)
    else:
        patch = sample_region(img_rgb, H_img_from_mm, cx + v, cy + u).transpose(1, 0, 2)
    return patch, u


def line_signal(patch: np.ndarray, u: np.ndarray, g: Grating, bed_margin_mm: float):
    """Project colours onto the bed->filament direction. Lines become positive.

    The bed colour is taken from the outer ``bed_margin_mm`` on both ends of the
    patch, which never contain lines.
    """
    extent = (g.n_lines - 1) / 2.0 * g.pitch
    outer = np.abs(u) > extent + g.pitch + 0.5 * bed_margin_mm
    inner = np.abs(u) < extent
    if outer.sum() < 2 or inner.sum() < 2:
        raise ValueError("patch too small")
    bed = np.median(patch[:, outer].reshape(-1, 3), axis=0)
    diff = patch - bed
    # Direction in colour space along which the inner region departs from the bed.
    # Use the mean of the strongest-deviating half of the pixels so the bed between
    # lines does not dilute it.
    inner_px = diff[:, inner].reshape(-1, 3)
    mag = np.linalg.norm(inner_px, axis=1)
    strong = inner_px[mag >= np.median(mag)]
    direction = strong.mean(axis=0)
    contrast = float(np.linalg.norm(direction))
    if contrast < 1e-6:
        return np.zeros(patch.shape[:2], np.float32), 0.0
    sig = diff @ (direction / contrast)
    return sig.astype(np.float32), contrast


def fit_phase(
    u: np.ndarray,
    profile: np.ndarray,
    pitch: float,
    center_mm: float,
    n_periods: int,
    harmonics: int = 4,
) -> PhaseFit:
    """Least-squares fit of a periodic signal plus a quadratic trend.

    Only the window of ``n_periods`` periods centred on ``center_mm`` is used. The
    phase comes from the fundamental; higher harmonics are fitted so that the
    narrow line profile does not leak into the residual (which drives the SNR).
    """
    half = n_periods * pitch / 2.0
    m = (u >= center_mm - half) & (u < center_mm + half)
    uu, yy = u[m], profile[m]
    w = 2.0 * math.pi / pitch
    t = (uu - center_mm) / half
    cols = [np.ones_like(uu), t, t * t]
    for h in range(1, harmonics + 1):
        cols += [np.cos(h * w * uu), np.sin(h * w * uu)]
    X = np.column_stack(cols)
    coef, *_ = np.linalg.lstsq(X, yy, rcond=None)
    resid = yy - X @ coef
    dof = max(len(yy) - X.shape[1], 1)
    s2 = float(resid @ resid) / dof
    b, c = coef[3], coef[4]
    amp = math.hypot(b, c)
    shift = pitch * math.atan2(c, b) / (2.0 * math.pi)
    # Covariance of (b, c) -> std of the phase. Samples are oversampled (correlated),
    # so this is optimistic; the band and per-line scatter are used as well.
    try:
        cov = np.linalg.inv(X.T @ X) * s2
        var_phase = (c * c * cov[3, 3] + b * b * cov[4, 4] - 2 * b * c * cov[3, 4]) / max(
            amp**4, 1e-18
        )
        sigma = pitch / (2.0 * math.pi) * math.sqrt(max(var_phase, 0.0))
    except np.linalg.LinAlgError:
        sigma = math.inf
    return PhaseFit(shift, sigma, amp, math.sqrt(s2))


def coarse_shift(u: np.ndarray, profile: np.ndarray, g: Grating, search_mm: float) -> float:
    """Shift of the grating envelope (centroid of the positive signal)."""
    extent = (g.n_lines - 1) / 2.0 * g.pitch + g.pitch / 2.0 + search_mm
    m = np.abs(u) <= extent
    w = np.clip(profile[m], 0, None)
    s = w.sum()
    return float((w * u[m]).sum() / s) if s > 0 else 0.0


def unwrap(shift: float, reference: float, pitch: float) -> float:
    return shift + pitch * round((reference - shift) / pitch)


def line_centers(u: np.ndarray, profile: np.ndarray, g: Grating, shift: float) -> np.ndarray:
    """Centroid of every line near its expected position (NaN if not found)."""
    out = np.full(g.n_lines, np.nan)
    hw = g.pitch / 3.0
    for i, k in enumerate(g.indices):
        expected = k * g.pitch + shift
        m = np.abs(u - expected) <= hw
        if m.sum() < 3:
            continue
        seg = profile[m]
        lo, hi = float(seg.min()), float(seg.max())
        if hi - lo <= 0:
            continue
        w = np.clip(seg - (lo + 0.5 * (hi - lo)), 0, None)
        if w.sum() > 0:
            out[i] = float((w * u[m]).sum() / w.sum())
    return out


def read_grating(
    img_rgb: np.ndarray,
    H_img_from_mm: np.ndarray,
    g: Grating,
    cfg: MeasureConfig,
    search_mm: float,
) -> GratingReading:
    patch, u = grating_patch(img_rgb, H_img_from_mm, g, cfg, search_mm)
    sig, contrast = line_signal(patch, u, g, bed_margin_mm=search_mm)
    profile = sig.mean(axis=0)
    coarse = coarse_shift(u, profile, g, search_mm)
    fit = fit_phase(u, profile, g.pitch, coarse, g.n_lines)
    shift = unwrap(fit.shift_mm, coarse, g.pitch)

    bands = np.array_split(np.arange(sig.shape[0]), max(cfg.bands, 2))
    band_shifts = []
    for rows in bands:
        bf = fit_phase(u, sig[rows].mean(axis=0), g.pitch, coarse, g.n_lines)
        band_shifts.append(unwrap(bf.shift_mm, shift, g.pitch))
    band_shifts_arr = np.array(band_shifts)
    sigma_bands = float(band_shifts_arr.std(ddof=1) / math.sqrt(len(band_shifts_arr)))

    centers = line_centers(u, profile, g, shift)
    # Per-line placement errors are common to all bands, so the band scatter cannot
    # see them. The scatter of the individual line centres around the fitted grating
    # captures them (plus noise).
    resid = centers - (g.indices * g.pitch + shift)
    resid = resid[np.isfinite(resid)]
    sigma_lines = float(resid.std(ddof=1) / math.sqrt(len(resid))) if len(resid) >= 5 else math.inf
    sigma = math.sqrt(max(sigma_bands, fit.sigma_mm) ** 2 + sigma_lines**2)
    return GratingReading(
        tool=g.tool,
        shift_mm=shift,
        sigma_mm=sigma,
        snr=fit.snr,
        coarse_mm=coarse,
        band_shifts_mm=band_shifts_arr.tolist(),
        line_indices=g.indices.copy(),
        line_centers_u=centers,
        contrast=contrast,
    )
