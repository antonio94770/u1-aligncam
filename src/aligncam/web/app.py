"""Web demo: load a photo (synthetic, uploaded, or from a printer through
Moonraker), show the detection overlay and the measured offsets, and walk
through the confirmation step of the dry-run offset writer.

Run with ``aligncam serve`` (``--fake`` uses an in-process fake Moonraker).
"""

from __future__ import annotations

import base64
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from aligncam.config import AppConfig
from aligncam.geometry import build_layout
from aligncam.measure import Measurement, measure_image
from aligncam.measure.overlay import draw_overlay
from aligncam.printer.moonraker import MoonrakerClient, MoonrakerError
from aligncam.printer.offsets import (
    Confirmation,
    ConfirmationError,
    DryRunWriter,
    OffsetProposal,
    build_proposal,
)
from aligncam.printer.workflow import decode_image, run_calibration
from aligncam.sim import Scenario, random_offsets, simulate
from aligncam.sim.degrade import Degradation

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class ApplyRequest(BaseModel):
    digest: str
    confirmed_by: str = "web user"
    tools: list[int] | None = None


def _data_url(img: np.ndarray, quality: int = 90) -> str:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def create_app(cfg: AppConfig | None = None, moonraker: MoonrakerClient | None = None) -> FastAPI:
    cfg = cfg or AppConfig()
    layout = build_layout(cfg.pattern)
    writer = DryRunWriter()
    proposals: dict[str, OffsetProposal] = {}
    app = FastAPI(title="U1 AlignCam demo")

    def respond(img: np.ndarray, m: Measurement, source: str, truth: dict | None = None) -> dict:
        proposal = build_proposal(
            m, cfg.printer.offset_correction_sign, pattern_rotation_deg=cfg.pattern.rotation_deg
        )
        proposals[proposal.digest()] = proposal
        return {
            "source": source,
            "image": _data_url(img),
            "overlay": _data_url(draw_overlay(img, layout, m, scale=2), quality=85),
            "measurement": m.to_dict(),
            "proposal": proposal.to_dict(),
            "preview": writer.describe([c for c in proposal.corrections if c.significant]),
            "truth": truth,
        }

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/info")
    def info() -> dict:
        return {
            "moonraker": None if moonraker is None else moonraker.base_url,
            "tools": list(cfg.pattern.tools),
            "writer": "dry-run",
            "correction_sign": cfg.printer.offset_correction_sign,
        }

    @app.get("/api/sample")
    def sample(
        seed: int = 0,
        max_offset_mm: float = 0.2,
        gsd: float = 0.15,
        tilt: float = 20.0,
        blur: float = 0.6,
    ) -> dict:
        if not (0.03 <= gsd <= 0.6 and 0 <= tilt <= 70 and 0 <= blur <= 5):
            raise HTTPException(422, "parameter out of range")
        rng = np.random.default_rng(seed + 7919)
        sc = Scenario(
            seed=seed,
            offsets_mm=random_offsets(rng, list(cfg.pattern.tools), max_offset_mm),
            gsd_mm_per_px=gsd,
            tilt_deg=tilt,
            degradation=Degradation(blur_sigma_px=blur),
        )
        img = simulate(sc, layout).image
        m = measure_image(img, layout, cfg.measure)
        truth = {f"T{t}": [round(v[0], 4), round(v[1], 4)] for t, v in sc.offsets_mm.items()}
        return respond(img, m, "synthetic", truth)

    @app.post("/api/measure")
    async def measure(file: UploadFile = File(...)) -> dict:
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "image too large")
        try:
            img = decode_image(data)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        m = measure_image(img, layout, cfg.measure, cfg.camera)
        return respond(img, m, f"upload: {file.filename}")

    @app.post("/api/printer/calibrate")
    def calibrate(print_pattern: bool = True) -> dict:
        if moonraker is None:
            raise HTTPException(400, "no Moonraker configured (start with --moonraker or --fake)")
        try:
            r = run_calibration(moonraker, cfg, print_pattern=print_pattern, poll_s=0.5)
        except (MoonrakerError, ValueError) as e:
            raise HTTPException(502, str(e)) from e
        return respond(r.image_bgr, r.measurement, f"printer: {moonraker.base_url}")

    @app.post("/api/offsets/apply")
    def apply(req: ApplyRequest) -> dict:
        proposal = proposals.get(req.digest)
        if proposal is None:
            raise HTTPException(404, "unknown or expired proposal")
        try:
            tools = None if req.tools is None else tuple(req.tools)
            res = writer.apply(proposal, Confirmation(req.digest, req.confirmed_by, tools))
        except ConfirmationError as e:
            raise HTTPException(409, str(e)) from e
        return {"applied": res.applied, "dry_run": res.dry_run, "lines": res.lines}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
