"""Regenerate the README media: UI screenshots, demo GIF and pipeline GIF.

    python tools/make_media.py [--browser PATH]

Everything is synthetic: the web demo runs against the in-process fake Moonraker.
Screenshots need a Chromium-based browser (Chrome or Edge) for headless capture.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import warnings
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from aligncam.config import MeasureConfig
from aligncam.geometry import build_layout
from aligncam.measure import measure_image
from aligncam.measure.grating import grating_patch, line_signal
from aligncam.measure.overlay import draw_overlay
from aligncam.sim import Scenario, simulate

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome",
    "chromium",
    "chromium-browser",
]
BG = (18, 18, 18)
TEXT = (236, 236, 236)
TEXT2 = (168, 168, 168)


# --------------------------------------------------------------------- server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_demo_server() -> str:
    import uvicorn

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient

    from aligncam.printer.fake_moonraker import FakePrinterState
    from aligncam.printer.fake_moonraker import create_app as fake_app
    from aligncam.printer.moonraker import MoonrakerClient
    from aligncam.web.app import create_app

    client = MoonrakerClient(
        "fake://in-process", client=TestClient(fake_app(FakePrinterState(print_seconds=0.5)))
    )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(moonraker=client), port=port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    return f"http://127.0.0.1:{port}"


def find_browser(explicit: str | None) -> str:
    for cand in [explicit, *BROWSERS]:
        if cand and (Path(cand).exists() or shutil.which(cand)):
            return cand
    raise SystemExit("no Chrome/Edge found; pass --browser")


def screenshot(browser: str, url: str, out: Path, size=(1440, 660)) -> Image.Image:
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--lang=en-US",
                f"--user-data-dir={profile}",
                f"--window-size={size[0]},{size[1]}",
                "--virtual-time-budget=20000",
                f"--screenshot={out}",
                url,
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )
    return Image.open(out).convert("RGB")


# --------------------------------------------------------------------- frames


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("segoeui.ttf", "DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def caption(img: Image.Image, title: str, sub: str = "", width: int = 960) -> Image.Image:
    img = img.copy()
    img.thumbnail((width, 10_000))
    canvas = Image.new("RGB", (width, img.height + 70), BG)
    canvas.paste(img, ((width - img.width) // 2, 70))
    d = ImageDraw.Draw(canvas)
    d.text((16, 10), title, fill=TEXT, font=_font(22))
    d.text((16, 40), sub, fill=TEXT2, font=_font(15))
    return canvas


def to_pil(bgr: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


def pad_same(frames: list[Image.Image]) -> list[Image.Image]:
    w = max(f.width for f in frames)
    h = max(f.height for f in frames)
    out = []
    for f in frames:
        c = Image.new("RGB", (w, h), BG)
        c.paste(f, (0, 0))
        out.append(c)
    return out


def save_gif(frames: list[Image.Image], path: Path, ms: int) -> None:
    frames = pad_same(frames)
    pal = [f.convert("P", palette=Image.Palette.ADAPTIVE, colors=256) for f in frames]
    pal[0].save(path, save_all=True, append_images=pal[1:], duration=ms, loop=0, optimize=True)


def pipeline_gif() -> None:
    layout = build_layout()
    sc = Scenario(seed=21, offsets_mm={1: (0.12, -0.08), 2: (-0.2, 0.05), 3: (0.04, 0.15)})
    img = simulate(sc, layout).image
    m = measure_image(img, layout)
    H = m.fiducials.H_img_from_mm

    f1 = caption(
        to_pil(img),
        "1. Photo of the printed pattern",
        "Synthetic: 0.15 mm/px, 20 deg tilt, true errors T1 (+120, -80) um ...",
    )
    fid = img.copy()
    for p in m.fiducials.image_points:
        cv2.circle(fid, tuple(np.round(p).astype(int)), 14, (255, 200, 0), 2, cv2.LINE_AA)
    x0, y0, x1, y1 = layout.bbox()
    for x in np.arange(np.floor(x0 / 10) * 10, x1, 10):
        a = cv2.perspectiveTransform(np.array([[[x, y0], [x, y1]]], float), H)[0]
        cv2.line(fid, tuple(a[0].astype(int)), tuple(a[1].astype(int)), (90, 90, 90), 1)
    for y in np.arange(np.floor(y0 / 10) * 10, y1, 10):
        a = cv2.perspectiveTransform(np.array([[[x0, y], [x1, y]]], float), H)[0]
        cv2.line(fid, tuple(a[0].astype(int)), tuple(a[1].astype(int)), (90, 90, 90), 1)
    f2 = caption(
        to_pil(fid),
        "2. Fiducials -> homography",
        "Four T0 disks + orientation dot give the pattern frame (10 mm grid shown)",
    )

    cell = next(c for c in layout.cells if c.name == "T1-x")
    img_rgb = img[..., ::-1].astype(np.float32) / 255.0
    rows = []
    for g in cell.gratings:
        patch, u = grating_patch(img_rgb, H, g, MeasureConfig(), 1.5)
        sig, *_ = line_signal(patch, u, g, 1.5)
        rows.append((patch, sig.mean(axis=0), u, g))
    # The vernier gratings have slightly different extents: centre-crop to a common width.
    w = min(r[0].shape[1] for r in rows)
    crops = []
    for patch, prof, u, g in rows:
        s = (patch.shape[1] - w) // 2
        crops.append((patch[:, s : s + w], prof[s : s + w], u[s : s + w], g))
    rows = crops
    strip = np.vstack([(np.clip(r[0], 0, 1) * 255).astype(np.uint8)[..., ::-1] for r in rows])
    strip = cv2.resize(strip, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_NEAREST)
    plot = np.full((260, strip.shape[1], 3), 18, np.uint8)
    colors = [(255, 255, 0), (255, 0, 255), (255, 255, 0)]
    for (_patch, prof, _u, _g), col, yoff in zip(rows, colors, (60, 140, 220), strict=True):
        p = (prof - prof.min()) / max(np.ptp(prof), 1e-6)
        xs = np.linspace(0, strip.shape[1] - 1, len(p))
        pts = np.stack([xs, yoff - p * 60], axis=1).astype(np.int32)
        cv2.polylines(plot, [pts], False, col, 1, cv2.LINE_AA)
    res = next(c for c in m.cells if c.name == "T1-x")
    f3 = caption(
        to_pil(np.vstack([strip, plot])),
        "3. Rectified cell T0 | T1 | T0 and line profiles",
        f"Phase fit: T1 shift vs mean of both T0 gratings = {res.offset_mm * 1000:+.0f} um "
        f"(vernier {res.vernier_offset_mm * 1000:+.0f} um, sigma {res.sigma_mm * 1000:.0f} um)",
    )
    ov = draw_overlay(img, layout, m, scale=2)
    t = m.tools
    f4 = caption(
        to_pil(ov),
        "4. Offsets with uncertainty, or a rejection",
        "  ".join(f"T{k}: {v.dx_mm * 1000:+.0f}/{v.dy_mm * 1000:+.0f} um" for k, v in t.items())
        + "   -> correction proposed, user must confirm",
    )
    save_gif([f1, f2, f3, f4], OUT / "pipeline.gif", ms=2600)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser")
    ap.add_argument("--skip-ui", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    pipeline_gif()
    print("pipeline.gif")
    if args.skip_ui:
        return
    browser = find_browser(args.browser)
    base = start_demo_server()
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        screenshot(browser, f"{base}/?seed=3&run=sample", OUT / "demo_ui.png")
        screenshot(browser, f"{base}/?run=printer&confirm=1", OUT / "demo_confirm.png")
        frames = []
        for gsd in (0.1, 0.2, 0.3, 0.4):
            shot = screenshot(browser, f"{base}/?seed=5&gsd={gsd}&run=sample", tmpd / f"g{gsd}.png")
            frames.append(
                caption(
                    shot,
                    f"Camera resolution: {gsd} mm per pixel",
                    "Same print, coarser camera: accuracy degrades, then the "
                    "measurement is rejected instead of guessed",
                    width=1100,
                )
            )
        save_gif(frames, OUT / "demo.gif", ms=2200)
    print("demo_ui.png demo_confirm.png demo.gif")


if __name__ == "__main__":
    main()
