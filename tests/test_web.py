import warnings

import cv2
import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from aligncam.printer.fake_moonraker import FakePrinterState
from aligncam.printer.fake_moonraker import create_app as fake_app
from aligncam.printer.moonraker import MoonrakerClient
from aligncam.sim import Scenario, simulate
from aligncam.web.app import create_app


@pytest.fixture(scope="module")
def web():
    state = FakePrinterState(print_seconds=0.05)
    client = MoonrakerClient("http://fake", client=TestClient(fake_app(state)))
    return TestClient(create_app(moonraker=client)), state


def test_index_and_info(web):
    c, _ = web
    assert "AlignCam" in c.get("/").text
    info = c.get("/api/info").json()
    assert info["writer"] == "dry-run" and info["moonraker"] == "http://fake"


def test_sample_returns_truth_and_overlay(web):
    c, _ = web
    r = c.get("/api/sample", params={"seed": 3}).json()
    assert r["image"].startswith("data:image/jpeg;base64,")
    assert set(r["truth"]) == {"T1", "T2", "T3"}
    assert r["measurement"]["status"] == "ok"
    for name, (tx, ty) in r["truth"].items():
        t = r["measurement"]["tools"][name]
        assert abs(t["dx_mm"] - tx) < 0.03 and abs(t["dy_mm"] - ty) < 0.03


def test_sample_parameter_validation(web):
    c, _ = web
    assert c.get("/api/sample", params={"gsd": 5}).status_code == 422


def test_upload(web, layout):
    c, _ = web
    img = simulate(Scenario(seed=4), layout).image
    ok, buf = cv2.imencode(".png", img)
    r = c.post("/api/measure", files={"file": ("x.png", buf.tobytes(), "image/png")})
    assert r.status_code == 200 and r.json()["measurement"]["status"] == "ok"
    bad = c.post("/api/measure", files={"file": ("x.png", b"not an image", "image/png")})
    assert bad.status_code == 422


def test_printer_flow_and_dry_run_apply(web):
    c, state = web
    r = c.post("/api/printer/calibrate").json()
    digest = r["proposal"]["digest"]
    assert c.post("/api/offsets/apply", json={"digest": "nope"}).status_code == 404
    res = c.post("/api/offsets/apply", json={"digest": digest, "tools": [2]}).json()
    assert res["dry_run"] is True and res["applied"] is False
    assert res["lines"][0].startswith("T2:")
    assert state.scripts == []
