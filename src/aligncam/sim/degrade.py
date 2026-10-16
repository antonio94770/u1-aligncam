"""Camera image formation: projection, optics, illumination and sensor degradations."""

from __future__ import annotations

import cv2
import numpy as np
from pydantic import BaseModel

from aligncam.sim.camera import PinholeCamera
from aligncam.sim.render import Texture


class Degradation(BaseModel):
    blur_sigma_px: float = 0.6
    """Gaussian approximation of defocus and lens blur, in output pixels."""
    illumination_gradient: float = 0.15
    """Peak-to-peak relative brightness change across the image (random direction)."""
    vignetting: float = 0.15
    """Relative brightness loss at the image corners."""
    exposure: float = 0.85
    """Scale applied before the sensor clips at 1.0."""
    shot_noise: float = 0.01
    """Std of the signal-dependent noise at full scale (Poisson approximation)."""
    read_noise: float = 0.006
    """Std of the signal-independent noise (fraction of full scale)."""
    jpeg_quality: int | None = 90
    """None disables JPEG compression."""


def project_texture(tex: Texture, cam: PinholeCamera, border_rgb: np.ndarray) -> np.ndarray:
    """Sample the texture for every (distorted) camera pixel. Returns float32 RGB."""
    w, h = cam.size
    u, v = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    pix = np.stack([u.ravel(), v.ravel()], axis=1)
    if cam.dist is not None:
        # distorted pixel -> undistorted pixel
        und = cv2.undistortPoints(pix.reshape(-1, 1, 2), cam.K, cam.dist, P=cam.K)
        pix = und.reshape(-1, 2)
    H_mm_from_img = np.linalg.inv(cam.homography())
    M = tex.tex_from_mm @ H_mm_from_img
    hom = np.column_stack([pix, np.ones(len(pix), np.float32)]) @ M.T
    mx = (hom[:, 0] / hom[:, 2]).reshape(h, w).astype(np.float32)
    my = (hom[:, 1] / hom[:, 2]).reshape(h, w).astype(np.float32)

    # Anti-aliasing: blur the texture to roughly one output pixel footprint.
    footprint = tex.px_per_mm * cam.gsd_at((0.0, 0.0))
    src = tex.rgb
    if footprint > 1.0:
        src = cv2.GaussianBlur(src, (0, 0), 0.5 * footprint)
    border = tuple(float(c) for c in border_rgb)
    return cv2.remap(
        src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=border
    )


def apply_degradation(img: np.ndarray, deg: Degradation, rng: np.random.Generator) -> np.ndarray:
    """Optics, illumination and sensor model. Input float RGB, output uint8 BGR."""
    h, w = img.shape[:2]
    out = img
    if deg.blur_sigma_px > 0:
        out = cv2.GaussianBlur(out, (0, 0), deg.blur_sigma_px)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xn = (xx - w / 2) / (w / 2)
    yn = (yy - h / 2) / (h / 2)
    ang = rng.uniform(0, 2 * np.pi)
    grad = 1.0 + 0.5 * deg.illumination_gradient * (np.cos(ang) * xn + np.sin(ang) * yn)
    vig = 1.0 - deg.vignetting * (xn**2 + yn**2) / 2.0
    out = out * (grad * vig * deg.exposure)[..., None]
    sigma = np.sqrt(np.clip(out, 0, None) * deg.shot_noise**2 + deg.read_noise**2)
    out = out + rng.normal(0.0, 1.0, out.shape).astype(np.float32) * sigma
    bgr = np.clip(out[..., ::-1] * 255.0 + 0.5, 0, 255).astype(np.uint8)
    if deg.jpeg_quality is not None:
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, int(deg.jpeg_quality)])
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return bgr
