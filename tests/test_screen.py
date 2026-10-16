"""Pattern and ChArUco board shown on a screen, for real-camera tests without a printer."""

import json
import tempfile
from pathlib import Path

import cv2
import numpy as np
from typer.testing import CliRunner

from aligncam.calib import make_board
from aligncam.cli import app
from aligncam.config import CharucoBoardConfig, MeasureConfig
from aligncam.measure import measure_image
from aligncam.screen import board_on_screen, pattern_on_screen, round_offsets

OFFSETS = {1: (0.1, -0.05), 2: (-0.2, 0.15), 3: (0.05, 0.25)}


def _max_error_um(img, layout, shown) -> float:
    m = measure_image(img, layout, MeasureConfig(), None)
    assert m.status == "ok", m.status
    return 1000 * max(
        max(abs(t.dx_mm - shown[k][0]), abs(t.dy_mm - shown[k][1])) for k, t in m.tools.items()
    )


def test_offsets_snap_to_whole_screen_pixels():
    s = 264 / 25.4
    for dx, dy in round_offsets(OFFSETS, s).values():
        assert abs(dx * s - round(dx * s)) < 1e-9 and abs(dy * s - round(dy * s)) < 1e-9


def test_pattern_at_actual_size_measures_back(layout):
    shot, shown = pattern_on_screen(layout, OFFSETS, (2360, 1640), 264)
    assert shot.image.shape == (1640, 2360, 3)
    assert shot.scale == 1.0
    assert _max_error_um(shot.image, layout, shown) < 5


def test_shrunk_portrait_pattern_measures_back(layout):
    """A pattern too large for the screen is shrunk; offsets stay in pattern mm."""
    shot, shown = pattern_on_screen(layout, OFFSETS, (1284, 2778), 458)
    assert shot.image.shape == (2778, 1284, 3)
    assert shot.scale < 1.0
    landscape = np.ascontiguousarray(np.rot90(shot.image, -1))
    assert _max_error_um(landscape, layout, shown) < 5


def test_board_on_screen_is_detected():
    cfg = CharucoBoardConfig()
    shot = board_on_screen(cfg, (2360, 1640), 264)
    assert shot.image.shape == (1640, 2360, 3)
    corners, ids, _, _ = cv2.aruco.CharucoDetector(make_board(cfg)).detectBoard(
        cv2.cvtColor(shot.image, cv2.COLOR_BGR2GRAY)
    )
    assert ids is not None and len(ids) == (cfg.squares_x - 1) * (cfg.squares_y - 1)


def test_cli_screen_target():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        args = ["screen-target", "--width-px", "2360", "--height-px", "1640", "--ppi", "264"]
        r = CliRunner().invoke(app, [*args, "--out", str(out), "--count", "2"])
        assert r.exit_code == 0, r.output
        names = sorted(p.name for p in out.iterdir())
        assert names == [
            "charuco.png",
            "pattern_01.json",
            "pattern_01.png",
            "pattern_02.json",
            "pattern_02.png",
        ]
        truth = json.loads((out / "pattern_01.json").read_text(encoding="utf-8"))
        assert set(truth["offsets_mm"]) == {"T1", "T2", "T3"}


def test_mono_pattern_has_one_colour_and_measures_back(layout):
    shot, shown = pattern_on_screen(layout, OFFSETS, (2360, 1640), 264, mono=True)
    img = shot.image.reshape(-1, 3).astype(int)
    # Every pixel mixes the bed and the reference colour (both have R = G).
    assert np.abs(img[:, 1] - img[:, 2]).max() <= 1
    assert _max_error_um(shot.image, layout, shown) < 5
