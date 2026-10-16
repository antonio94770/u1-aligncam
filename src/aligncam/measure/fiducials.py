"""Fiducial detection and the image <-> pattern homography.

The pattern has four corner disks plus a smaller orientation dot next to the
top-left disk, all printed by the reference tool. The disks are found as
circular blobs that differ from the bed colour; the orientation dot fixes the
corner order. No assumption is made on the colours or on the image scale.

Two detectors run in turn. The fast one takes the dominant colour of the whole
image as the bed and cuts the contrast map with one Otsu threshold. It fails when
the bed is not the dominant colour (the frame shows a lot of chamber, or the
pattern sits on a screen in a dark room) or when exposure squeezes the contrast.
The fallback compares every pixel with its local background, thresholds at several
levels and keeps only round blobs surrounded by clean bed, which is how the
fiducials are laid out (nothing printed within 2 mm of a disk edge).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import cv2
import numpy as np

from aligncam.geometry import Layout


@dataclass
class Blob:
    center: np.ndarray
    area: float
    circularity: float


@dataclass
class FiducialFit:
    image_points: np.ndarray
    """(5, 2) pixel centres: TL, TR, BR, BL corner disks, then the orientation dot."""
    H_img_from_mm: np.ndarray
    reproj_rms_px: float
    disk_radius_px: float


def contrast_map(img_bgr: np.ndarray) -> np.ndarray:
    """Per-pixel colour distance from the dominant (bed) colour, in Lab units."""
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    bg = np.median(lab.reshape(-1, 3)[:: max(1, lab.shape[0] * lab.shape[1] // 50000)], axis=0)
    d = np.linalg.norm(lab - bg, axis=2)
    return cv2.GaussianBlur(d, (0, 0), 1.0)


def find_blobs(cmap: np.ndarray, min_area: float = 20.0, min_circularity: float = 0.6):
    scaled = np.clip(cmap / max(float(cmap.max()), 1e-6) * 255.0, 0, 255).astype(np.uint8)
    _, binary = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    blobs = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        perim = cv2.arcLength(c, True)
        circ = 4.0 * math.pi * area / (perim * perim) if perim > 0 else 0.0
        hull_area = cv2.contourArea(cv2.convexHull(c))
        if circ < min_circularity or hull_area <= 0 or area / hull_area < 0.9:
            continue
        m = cv2.moments(c)
        blobs.append(Blob(np.array([m["m10"] / m["m00"], m["m01"] / m["m00"]]), area, circ))
    return blobs


def refine_center(cmap: np.ndarray, center: np.ndarray, radius: float) -> np.ndarray:
    """Background-subtracted, intensity-weighted centroid around a blob."""
    r_in, r_out = 1.25 * radius, 1.8 * radius
    x0 = int(max(center[0] - r_out, 0))
    y0 = int(max(center[1] - r_out, 0))
    x1 = int(min(center[0] + r_out + 1, cmap.shape[1]))
    y1 = int(min(center[1] + r_out + 1, cmap.shape[0]))
    patch = cmap[y0:y1, x0:x1]
    yy, xx = np.mgrid[y0:y1, x0:x1]
    rr = np.hypot(xx - center[0], yy - center[1])
    ring = patch[(rr > r_in) & (rr <= r_out)]
    bg = float(np.median(ring)) if ring.size else 0.0
    w = np.clip(patch - bg, 0, None) * (rr <= r_in)
    s = w.sum()
    if s <= 0:
        return center
    return np.array([(w * xx).sum() / s, (w * yy).sum() / s])


def _order_clockwise(pts: np.ndarray) -> np.ndarray:
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    return np.argsort(ang)


def _is_convex(quad: np.ndarray) -> bool:
    signs = []
    for i in range(4):
        a, b, c = quad[i], quad[(i + 1) % 4], quad[(i + 2) % 4]
        e1, e2 = b - a, c - b
        signs.append(e1[0] * e2[1] - e1[1] * e2[0])
    return all(s > 0 for s in signs) or all(s < 0 for s in signs)


MAX_CORNER_AREA_RATIO = 8.0
"""Largest/smallest corner disk area. Oblique side views (a camera on a side panel)
make near disks several times larger than far ones; the homography and the
orientation-dot prediction reject wrong quads, so this is only a coarse prune."""


def local_contrast_map(img_bgr: np.ndarray, window_frac: float = 0.1) -> np.ndarray:
    """Per-pixel colour distance from the local background, in Lab units.

    The background is a median over a window of ``window_frac`` of the image size,
    computed on a reduced copy. A disk covers at most about 4 % of the pattern width,
    so even a pattern that fills the frame leaves the window mostly bed.
    """
    h, w = img_bgr.shape[:2]
    scale = min(1.0, 480.0 / max(h, w))
    small = cv2.resize(
        img_bgr, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA
    )
    k = max(5, int(round(window_frac * max(small.shape[:2]))) | 1)
    lab_small = cv2.cvtColor(small, cv2.COLOR_BGR2LAB)
    bg = cv2.merge([cv2.medianBlur(c, k) for c in cv2.split(lab_small)])
    bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    d = np.linalg.norm(lab - bg, axis=2)
    return cv2.GaussianBlur(d, (0, 0), 1.0)


LEVELS = (0.12, 0.2, 0.3, 0.42, 0.56, 0.72)
"""Thresholds of the fallback detector, as fractions of the 99.9th contrast percentile."""


def find_isolated_blobs(
    cmap: np.ndarray, min_area: float = 20.0, min_circularity: float = 0.7
) -> list[Blob]:
    """Round, solid blobs with a clean ring of background around them, over several
    thresholds. Line fragments of the gratings fail the ring test."""
    top = float(np.percentile(cmap, 99.9))
    if top <= 0:
        return []
    found: list[tuple[Blob, float]] = []
    for level in LEVELS:
        t = level * top
        binary = (cmap > t).astype(np.uint8)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        for c in contours:
            area = cv2.contourArea(c)
            if area < min_area:
                continue
            perim = cv2.arcLength(c, True)
            circ = 4.0 * math.pi * area / (perim * perim) if perim > 0 else 0.0
            hull_area = cv2.contourArea(cv2.convexHull(c))
            if circ < min_circularity or hull_area <= 0 or area / hull_area < 0.9:
                continue
            if not _ring_is_clean(binary, c, area):
                continue
            m = cv2.moments(c)
            centre = np.array([m["m10"] / m["m00"], m["m01"] / m["m00"]])
            found.append((Blob(centre, area, circ), level))
    # The same disk shows up at several levels: keep its roundest outline.
    blobs: list[Blob] = []
    for b, _ in sorted(found, key=lambda bl: -bl[0].circularity):
        r = math.sqrt(b.area / math.pi)
        if all(
            np.hypot(*(b.center - o.center)) > 0.7 * max(r, math.sqrt(o.area / math.pi))
            for o in blobs
        ):
            blobs.append(b)
    return blobs


def _ring_is_clean(binary: np.ndarray, contour: np.ndarray, area: float) -> bool:
    """Less than 10 % of a ring from 0.25 to 0.55 blob radii outside the blob is
    above the threshold. (The orientation dot is 0.67 disk radii from its disk.)"""
    r = math.sqrt(area / math.pi)
    inner, outer = max(1, int(round(0.25 * r))), max(2, int(round(0.55 * r)))
    x, y, w, h = cv2.boundingRect(contour)
    pad = outer + 1
    x0, y0 = max(x - pad, 0), max(y - pad, 0)
    x1, y1 = min(x + w + pad, binary.shape[1]), min(y + h + pad, binary.shape[0])
    mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
    cv2.drawContours(mask, [contour - np.array([x0, y0])], -1, 1, thickness=cv2.FILLED)
    near = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * inner + 1,) * 2))
    far = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * outer + 1,) * 2))
    ring = (far > 0) & (near == 0)
    if not ring.any():
        return False
    return float(binary[y0:y1, x0:x1][ring].mean()) < 0.1


def find_fiducials(img_bgr: np.ndarray, layout: Layout, max_candidates: int = 10):
    """Locate the fiducials and fit the homography. Returns None when not found."""
    cmap = contrast_map(img_bgr)
    blobs = sorted(find_blobs(cmap), key=lambda b: -b.area)[:max_candidates]
    fit = _fit_fiducials(cmap, blobs, layout)
    if fit is None:
        cmap = local_contrast_map(img_bgr)
        blobs = sorted(find_isolated_blobs(cmap), key=lambda b: -b.area)[:16]
        fit = _fit_fiducials(cmap, blobs, layout)
    return fit


def _fit_fiducials(cmap: np.ndarray, blobs: list[Blob], layout: Layout) -> FiducialFit | None:
    if len(blobs) < 5:
        return None
    model = layout.fiducial_points()
    corner_model = model[:4].astype(np.float32)
    dot_model = model[4]
    expected_ratio = (layout.orientation_dot.diameter / layout.fiducials[0].diameter) ** 2

    best = None
    for quad_idx in itertools.combinations(range(len(blobs)), 4):
        areas = np.array([blobs[i].area for i in quad_idx])
        if areas.max() / areas.min() > MAX_CORNER_AREA_RATIO:
            continue
        pts = np.array([blobs[i].center for i in quad_idx])
        order = _order_clockwise(pts)
        quad, quad_areas = pts[order], areas[order]
        if not _is_convex(quad):
            continue
        for rot in range(4):
            q = np.roll(quad, -rot, axis=0).astype(np.float32)
            radii = np.sqrt(np.roll(quad_areas, -rot) / math.pi)
            H = cv2.getPerspectiveTransform(corner_model, q)
            pred = cv2.perspectiveTransform(dot_model.reshape(1, 1, 2), H).reshape(2)
            # The dot sits next to the first corner: compare it with that disk, which
            # is seen at nearly the same scale.
            ref_area = math.pi * radii[0] ** 2
            for j, b in enumerate(blobs):
                if j in quad_idx:
                    continue
                ratio = b.area / ref_area
                if not 0.4 * expected_ratio < ratio < 2.5 * expected_ratio:
                    continue
                err = float(np.hypot(*(b.center - pred))) / radii[0]
                if err < 0.5 and (best is None or err < best[0]):
                    best = (err, q, b.center, radii)
    if best is None:
        return None

    _, quad, dot_c, radii = best
    centers = [refine_center(cmap, p, r) for p, r in zip(quad, radii, strict=True)]
    centers.append(refine_center(cmap, dot_c, radii[0] * math.sqrt(expected_ratio)))
    img_pts = np.array(centers, dtype=np.float64)
    H, _ = cv2.findHomography(model.astype(np.float64), img_pts, 0)
    proj = cv2.perspectiveTransform(model.reshape(-1, 1, 2), H).reshape(-1, 2)
    rms = float(np.sqrt(np.mean(np.sum((proj - img_pts) ** 2, axis=1))))
    return FiducialFit(img_pts, H, rms, float(np.mean(radii)))
