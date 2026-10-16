"""Lens calibration from a ChArUco board, on synthetic photos with a known lens."""

import math
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

from aligncam.bench.runner import recovered_calibration
from aligncam.bench.sweeps import sweep_cases
from aligncam.calib import CalibReason, calibrate_images, make_board
from aligncam.calib.board import board_svg, rasterize
from aligncam.calib.calibrate import ViewDetection, calibrate_views
from aligncam.cli import app
from aligncam.config import CalibrationConfig, CharucoBoardConfig, MeasureConfig, load_config
from aligncam.geometry import build_layout
from aligncam.measure import measure_image
from aligncam.sim import Scenario, simulate
from aligncam.sim.board import board_views, view_poses
from aligncam.sim.u1 import PLACEMENTS, side_camera_pose

SIZE = (960, 540)
"""A small sensor keeps the tests fast; the method does not depend on the size."""
HFOV_DEG = 90.0
K1 = -0.1


def _K(size=SIZE, hfov=HFOV_DEG) -> np.ndarray:
    f = (size[0] / 2) / math.tan(math.radians(hfov) / 2)
    return np.array([[f, 0, size[0] / 2], [0, f, size[1] / 2], [0, 0, 1.0]])


DIST = np.array([K1, 0.0, 0.0, 0.0, 0.0])


@pytest.fixture
def workdir():
    """Plain temporary directory (independent of pytest's shared basetemp)."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture(scope="module")
def photos():
    return board_views(_K(), DIST, SIZE, CharucoBoardConfig(), np.random.default_rng(7), 12)


def _projected_views(poses, noise_px=0.05, seed=0) -> list[ViewDetection]:
    """Exact corner projections (plus noise): fast inputs for the quality gates."""
    rng = np.random.default_rng(seed)
    obj = make_board(CharucoBoardConfig()).getChessboardCorners().astype(np.float32)
    views = []
    for i, (R, t) in enumerate(poses):
        img, _ = cv2.projectPoints(obj, cv2.Rodrigues(R)[0], t, _K(), DIST)
        img = img.reshape(-1, 2) + rng.normal(0, noise_px, (len(obj), 2))
        inside = np.all((img >= 0) & (img < SIZE), axis=1)
        views.append(ViewDetection(f"v{i}", obj[inside], img[inside].astype(np.float32)))
    return views


def _poses(n=12, **kw):
    return view_poses(_K(), SIZE, CharucoBoardConfig(), np.random.default_rng(3), n, **kw)


def test_board_raster_matches_opencv():
    cfg = CharucoBoardConfig()
    ours = rasterize(cfg, 4.0)
    h, w = ours.shape
    ref = make_board(cfg).generateImage((w, h), marginSize=0, borderBits=1) / 255.0
    assert np.mean(np.abs(ours - ref)) < 0.02


def test_board_svg_has_physical_size():
    svg = board_svg(CharucoBoardConfig(), margin_mm=10.0)
    assert 'width="245mm"' in svg and "<rect" in svg


def test_unknown_dictionary_is_refused():
    with pytest.raises(ValueError, match="dictionary"):
        make_board(CharucoBoardConfig(dictionary="DICT_NOPE"))


def test_recovers_k1_and_focal(photos):
    res = calibrate_images(photos, CalibrationConfig(distortion_model="k1"))
    assert res.ok, res.messages
    K = res.camera_matrix
    assert abs(res.dist_coeffs[0] - K1) < 0.005
    assert abs(K[0, 0] / _K()[0, 0] - 1) < 0.005
    assert np.allclose(K[:2, 2], _K()[:2, 2], atol=5.0)
    assert res.rms_px < 0.3


def test_default_model_undistorts_like_the_true_lens(photos):
    res = calibrate_images(photos)
    assert res.ok, res.messages
    u, v = np.meshgrid(np.linspace(20, SIZE[0] - 20, 15), np.linspace(20, SIZE[1] - 20, 9))
    pix = np.stack([u.ravel(), v.ravel()], axis=1).reshape(-1, 1, 2)
    true = cv2.undistortPoints(pix, _K(), DIST, P=_K()).reshape(-1, 2)
    K = res.camera_matrix
    est = cv2.undistortPoints(pix, K, res.dist_coeffs, P=K).reshape(-1, 2)
    # A projective difference (e.g. a slightly different focal length) is absorbed by
    # the fiducial homography of the measurement; only the rest is an error.
    H, _ = cv2.findHomography(est, true)
    fit = cv2.perspectiveTransform(est.reshape(-1, 1, 2), H).reshape(-1, 2)
    err = np.hypot(*(fit - true).T)
    # The extreme image corners are extrapolated by k2 and p1/p2.
    assert np.percentile(err, 90) < 0.2 and err.max() < 1.0


def test_yaml_snippet_round_trips(photos, workdir):
    res = calibrate_images(photos)
    path = workdir / "calib.yaml"
    path.write_text(res.to_yaml(), encoding="utf-8")
    root = Path(__file__).resolve().parents[1] / "config"
    cfg = load_config(root / "camera.u1.template.yaml", path)
    assert np.allclose(cfg.camera.camera_matrix, res.camera_matrix, atol=1e-3)
    assert np.allclose(cfg.camera.dist_coeffs, res.dist_coeffs, atol=1e-5)
    assert cfg.camera.resolution_px == SIZE


def test_gate_too_few_views():
    res = calibrate_views(_projected_views(_poses(5)), SIZE, CalibrationConfig())
    assert res.status == "rejected" and CalibReason.TOO_FEW_VIEWS in res.reasons
    assert res.camera_matrix is not None


def test_gate_high_reprojection():
    res = calibrate_views(_projected_views(_poses(), noise_px=2.0), SIZE, CalibrationConfig())
    assert CalibReason.HIGH_REPROJECTION in res.reasons


def test_gate_poor_coverage():
    # Small board, always near the image centre.
    poses = [(R, t * np.array([0.0, 0.0, 2.0])) for R, t in _poses(12, board_fraction=0.4)]
    poses = [(R, t - R @ np.array([112.5, 75.0, 0.0])) for R, t in poses]
    res = calibrate_views(_projected_views(poses), SIZE, CalibrationConfig())
    assert CalibReason.POOR_COVERAGE in res.reasons
    assert any("corner" in m for m in res.messages)


def test_gate_low_tilt_spread():
    res = calibrate_views(_projected_views(_poses(tilt_deg=(0.0, 3.0))), SIZE, CalibrationConfig())
    assert CalibReason.LOW_TILT_SPREAD in res.reasons


def test_good_projected_views_pass():
    res = calibrate_views(_projected_views(_poses()), SIZE, CalibrationConfig())
    assert res.ok, res.messages


def test_cli_calibrate_camera(photos, workdir):
    folder = workdir / "photos"
    folder.mkdir()
    for name, img in photos:
        cv2.imwrite(str(folder / name), img)
    out = workdir / "calib.yaml"
    runner = CliRunner()
    r = runner.invoke(app, ["calibrate-camera", str(folder), "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert "camera_matrix" in out.read_text(encoding="utf-8")

    for p in sorted(folder.iterdir())[3:]:
        p.unlink()
    out.unlink()
    r = runner.invoke(app, ["calibrate-camera", str(folder), "--out", str(out)])
    assert r.exit_code == 1
    assert "too_few_views" in r.output and not out.exists()


def test_cli_calibration_board(workdir):
    out = workdir / "board.svg"
    r = CliRunner().invoke(app, ["calibration-board", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert out.read_text(encoding="utf-8").startswith("<svg")


def test_benchmark_calibrated_cases_reuse_u1_scenes():
    n = 2
    cases = list(sweep_cases(n))
    u1 = [(c[1], c[3]) for c in cases if c[0] == "u1"]
    for name in ("u1_cal", "u1_rcal"):
        assert [(c[1], c[3]) for c in cases if c[0] == name] == u1
    assert cases[-1][0] == "u1_rcal"
    # The sweeps after "u1" keep the seeds they had before the pairing.
    colors = [c[3] for c in cases if c[0] == "colors"]
    assert colors[0] == u1[-1][1] + 1 + len(PLACEMENTS) * n


def test_recovered_calibration_in_cropped_image_coordinates():
    lay = build_layout(PLACEMENTS[2].pattern_config())
    pose = side_camera_pose(150.0, 30.0, 90.0).model_copy(
        update={"width_px": 960, "height_px": 540}
    )
    sc = Scenario(seed=1, pose=pose, k1=-0.1)
    res = simulate(sc, lay)
    camera, status = recovered_calibration(res.camera, sc, seed=1)
    assert status == "ok"
    K = np.asarray(camera.camera_matrix)
    assert np.allclose(K[:2, 2], res.camera.K[:2, 2], atol=5.0)
    assert abs(K[0, 0] / res.camera.K[0, 0] - 1) < 0.01
    # At this coarse GSD the uncorrected distortion breaks every cell (see the u1 benchmark).
    raw = measure_image(res.image, lay, MeasureConfig())
    cal = measure_image(res.image, lay, MeasureConfig(), camera)
    n_ok = [sum(t.status == "ok" for t in m.tools.values()) for m in (raw, cal)]
    assert n_ok[1] > n_ok[0], n_ok
