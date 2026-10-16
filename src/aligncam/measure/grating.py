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
    asymmetry: float = 0.0
    """Odd (asymmetric) part of the line profile from the 2nd harmonic, relative to
    the fundamental. 0 for symmetric lines. One-sided lighting of the beads makes it
    non-zero, and it also shifts the apparent line centre."""
    evenness: float = 0.0
    """Even part of the 2nd harmonic relative to the fundamental. Lines are narrower
    than the gaps between them, so this is positive when the signal has the right
    sign (lines = peaks) and negative when it is inverted, which would shift the
    reading by half a pitch."""

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
    asymmetry: float = 0.0
    evenness: float = 0.0
    luma_contrast: float = 0.0
    """Signed luminance difference lines minus bed (>0: lines lighter than the bed)."""
    shading_sensitivity: float = 0.0
    """|line colour| / |line colour - bed colour| along the signal direction. Large
    values mean bead shading is strong compared with the useful contrast."""


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


LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


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
    luma = float(direction @ LUMA)
    if contrast < 1e-6:
        return np.zeros(patch.shape[:2], np.float32), 0.0, 0.0, math.inf
    unit = direction / contrast
    sig = diff @ unit
    # Multiplicative shading of the bead changes its colour in proportion to the
    # colour itself, while the useful signal is the difference from the bed. Their
    # ratio says how strongly lighting can move the apparent line centre.
    line_color = bed + direction
    sensitivity = float(abs(line_color @ unit) / contrast)
    return sig.astype(np.float32), contrast, luma, sensitivity


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
    # Linear phase/amplitude drift of the fundamental: absorbs a local scale error
    # (e.g. lens distortion) so the phase is read at the grating centre.
    cols += [t * np.cos(w * uu), t * np.sin(w * uu)]
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
    asym = even = 0.0
    if harmonics >= 2 and amp > 0:
        b2, c2 = coef[5], coef[6]
        d2 = math.atan2(c2, b2) - 2.0 * math.atan2(c, b)
        asym = math.hypot(b2, c2) / amp * math.sin(d2)
        even = math.hypot(b2, c2) / amp * math.cos(d2)
    return PhaseFit(shift, sigma, amp, math.sqrt(s2), asym, even)


def coarse_shift(u: np.ndarray, profile: np.ndarray, g: Grating, search_mm: float) -> float:
    """Shift of the grating as a whole, independent of the phase.

    The local modulation (max - min over one pitch) is high over the grating and
    low on the bare bed. The midpoint between the two places where it crosses
    half of its typical value is the grating centre. Using edges instead of a
    centroid makes the estimate insensitive to slow brightness gradients.
    """
    step = float(u[1] - u[0])
    win = max(int(round(g.pitch / step)), 3)
    if len(profile) <= win:
        return 0.0
    view = np.lib.stride_tricks.sliding_window_view(profile, win)
    env = view.max(axis=1) - view.min(axis=1)
    uc = u[win // 2 : win // 2 + len(env)]
    half = (g.n_lines - 1) / 2.0 * g.pitch
    inner = np.abs(uc) < 0.6 * half
    if not inner.any():
        return 0.0
    level = 0.5 * float(np.median(env[inner]))
    above = env > level
    if not above.any():
        return 0.0
    i0 = int(np.argmax(above))
    i1 = len(above) - 1 - int(np.argmax(above[::-1]))

    def cross(i_out: int, i_in: int) -> float:
        a, b = env[i_out], env[i_in]
        t = 0.0 if b == a else (level - a) / (b - a)
        return float(uc[i_out] + t * (uc[i_in] - uc[i_out]))

    left = cross(i0 - 1, i0) if i0 > 0 else float(uc[0])
    right = cross(i1 + 1, i1) if i1 < len(env) - 1 else float(uc[-1])
    return 0.5 * (left + right)


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


def _trend_scatter(x: np.ndarray, y: np.ndarray, degree: int) -> float:
    """Standard error of the mean of ``y`` after removing a polynomial trend in ``x``."""
    n = len(y)
    if n < degree + 3:
        return math.inf
    coef = np.polyfit(x, y, degree)
    resid = y - np.polyval(coef, x)
    s = math.sqrt(float(resid @ resid) / (n - degree - 1))
    return s / math.sqrt(n)


def read_grating(
    img_rgb: np.ndarray,
    H_img_from_mm: np.ndarray,
    g: Grating,
    cfg: MeasureConfig,
    search_mm: float,
    unwrap_to: float | None = None,
) -> GratingReading:
    """Read one grating.

    The phase is known modulo one pitch. ``unwrap_to`` picks the period: the
    caller passes a value it trusts (0 for the reference tool, which also printed
    the fiducials; the reference shift for the measured tool). Without it the
    envelope centroid is used, which is much less reliable at low contrast.
    """
    patch, u = grating_patch(img_rgb, H_img_from_mm, g, cfg, search_mm)
    sig, contrast, luma, sensitivity = line_signal(patch, u, g, bed_margin_mm=search_mm)
    profile = sig.mean(axis=0)
    coarse = coarse_shift(u, profile, g, search_mm)
    anchor = coarse if unwrap_to is None else unwrap_to
    fit = fit_phase(u, profile, g.pitch, anchor, g.n_lines)
    shift = unwrap(fit.shift_mm, anchor, g.pitch)

    bands = np.array_split(np.arange(sig.shape[0]), max(cfg.bands, 2))
    band_shifts = []
    for rows in bands:
        bf = fit_phase(u, sig[rows].mean(axis=0), g.pitch, shift, g.n_lines)
        band_shifts.append(unwrap(bf.shift_mm, shift, g.pitch))
    band_shifts_arr = np.array(band_shifts)
    # A linear trend across the bands is a shear of the image (smooth error field),
    # which the symmetric cell layout cancels; only the scatter around it is noise.
    band_pos = np.array([rows.mean() for rows in bands], float)
    sigma_bands = _trend_scatter(band_pos, band_shifts_arr, degree=1)

    centers = line_centers(u, profile, g, shift)
    # Per-line placement errors are common to all bands, so the band scatter cannot
    # see them. The scatter of the individual line centres around the fitted grating
    # captures them (plus noise).
    resid = centers - (g.indices * g.pitch + shift)
    ok = np.isfinite(resid)
    # Slow variations along the grating (scale error, distortion) are removed with a
    # quadratic before measuring the line-to-line scatter.
    sigma_lines = _trend_scatter(g.indices[ok].astype(float), resid[ok], degree=2)
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
        asymmetry=fit.asymmetry,
        evenness=fit.evenness,
        luma_contrast=luma,
        shading_sensitivity=sensitivity,
    )
