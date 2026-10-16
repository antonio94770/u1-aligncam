"""A fake Moonraker for development without a printer.

It implements the handful of endpoints the client uses. The "printer" has hidden
tool errors. After the calibration file is "printed", the webcam returns a
synthetic photo of the pattern rendered with those errors, so the whole
workflow (upload, print, snapshot, measure, propose) can run end to end.

Every G-code script sent to it is logged in ``state.scripts`` so tests can
check that nothing tries to change offsets behind the user's back.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from aligncam.config import PatternConfig
from aligncam.geometry import build_layout
from aligncam.sim import Scenario, simulate
from aligncam.sim.render import Appearance


@dataclass
class FakePrinterState:
    hidden_offsets_mm: dict[int, tuple[float, float]] = field(
        default_factory=lambda: {1: (0.06, -0.04), 2: (-0.11, 0.03), 3: (0.02, 0.09)}
    )
    print_seconds: float = 1.0
    seed: int = 1234
    gsd_mm_per_px: float = 0.15
    files: dict[str, str] = field(default_factory=dict)
    scripts: list[str] = field(default_factory=list)
    printing: str | None = None
    printed: str | None = None
    started_at: float = 0.0
    _cache: dict[str, bytes] = field(default_factory=dict)

    def state(self) -> str:
        if self.printing is None:
            return "complete" if self.printed else "standby"
        if time.monotonic() - self.started_at >= self.print_seconds:
            self.printed, self.printing = self.printing, None
            return "complete"
        return "printing"


def create_app(
    state: FakePrinterState | None = None, pattern: PatternConfig | None = None
) -> FastAPI:
    st = state or FakePrinterState()
    layout = build_layout(pattern or PatternConfig())
    app = FastAPI(title="Fake Moonraker (AlignCam development)")
    app.state.printer = st

    @app.get("/server/info")
    def server_info():
        return {"result": {"klippy_state": "ready", "moonraker_version": "fake-aligncam"}}

    @app.get("/printer/objects/query")
    def query(print_stats: str = ""):
        return {
            "result": {
                "status": {
                    "print_stats": {
                        "state": st.state(),
                        "filename": st.printing or st.printed or "",
                        "message": "",
                    }
                }
            }
        }

    @app.post("/server/files/upload")
    async def upload(file: UploadFile = File(...), root: str = Form("gcodes")):
        st.files[file.filename] = (await file.read()).decode("utf-8")
        return {"result": {"item": {"path": file.filename, "root": root}, "action": "create_file"}}

    @app.post("/printer/print/start")
    def start(filename: str):
        if filename not in st.files:
            raise HTTPException(404, f"file {filename} not found")
        if st.state() == "printing":
            raise HTTPException(400, "printer busy")
        st.printing, st.started_at = filename, time.monotonic()
        st._cache.clear()
        return {"result": "ok"}

    @app.post("/printer/gcode/script")
    def gcode_script(script: str):
        st.scripts.append(script)
        return {"result": "ok"}

    @app.get("/server/webcams/list")
    def webcams():
        return {
            "result": {
                "webcams": [{"name": "chamber (fake)", "snapshot_url": "/webcam/?action=snapshot"}]
            }
        }

    @app.get("/webcam/")
    def snapshot(action: str = "snapshot"):
        st.state()
        key = st.printed or "empty"
        if key not in st._cache:
            st._cache[key] = _render(st, layout, printed=st.printed is not None)
        return Response(st._cache[key], media_type="image/jpeg")

    return app


def _render(st: FakePrinterState, layout, printed: bool) -> bytes:
    app = Appearance() if printed else Appearance(contrast={t: 0.0 for t in range(4)})
    sc = Scenario(
        seed=st.seed,
        offsets_mm=dict(st.hidden_offsets_mm),
        gsd_mm_per_px=st.gsd_mm_per_px,
        appearance=app,
    )
    img = simulate(sc, layout).image
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    return np.asarray(buf).tobytes()
