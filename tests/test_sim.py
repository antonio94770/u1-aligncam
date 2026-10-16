import cv2
import numpy as np

from aligncam.geometry import build_layout
from aligncam.sim import Scenario, simulate
from aligncam.sim.camera import camera_for_gsd, distance_for_gsd, gsd_for_camera
from aligncam.sim.degrade import Degradation, project_texture
from aligncam.sim.render import render_texture
from aligncam.sim.u1 import PLACEMENTS, side_camera_pose


def test_deterministic(layout):
    a = simulate(Scenario(seed=1, gsd_mm_per_px=0.3), layout).image
    b = simulate(Scenario(seed=1, gsd_mm_per_px=0.3), layout).image
    assert np.array_equal(a, b)


def test_gsd_and_orientation():
    cam = camera_for_gsd(0.2, (-50, -30, 50, 30), tilt_deg=0)
    assert abs(cam.gsd_at((0, 0)) - 0.2) < 1e-3
    p = cam.project(np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0]]))
    assert p[1][0] > p[0][0]  # +X to the right
    assert p[2][1] < p[0][1]  # +Y up in the image


def test_homography_matches_projection_without_distortion():
    cam = camera_for_gsd(0.15, (-50, -30, 50, 30), tilt_deg=30, roll_deg=7)
    pts = np.array([[-40.0, 20.0], [35.0, -25.0]])
    hom = np.column_stack([pts, np.ones(2)]) @ cam.homography().T
    assert np.allclose(hom[:, :2] / hom[:, 2:], cam.project(pts), atol=1e-6)


def test_camera_helpers_roundtrip():
    d = distance_for_gsd(1920, 70.0, 0.1)
    assert abs(gsd_for_camera(1920, 70.0, d) - 0.1) < 1e-12


def test_fiducials_visible(layout):
    r = simulate(Scenario(seed=0), layout)
    img = r.image.astype(float).mean(axis=2)
    bed = np.median(img)
    for x, y in r.truth()["fiducials_px"]:
        assert img[int(round(y)), int(round(x))] > bed + 60


def test_folded_lens_region_rendered_as_plate():
    """Past the fold radius of a strong k1 the k1-only model has no pre-image;
    without masking those pixels showed ghost copies of the scene."""
    lay = build_layout(PLACEMENTS[3].pattern_config())
    clean = Degradation(
        blur_sigma_px=0, illumination_gradient=0, vignetting=0, exposure=1,
        shot_noise=0, read_noise=0, jpeg_quality=None,
    )  # fmt: skip
    sc = Scenario(seed=0, pose=side_camera_pose(50.0, 20.0, 120.0), k1=-0.15, degradation=clean)
    res = simulate(sc, lay)
    cam = res.camera
    w, h = cam.size
    u, v = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    pix = np.stack([u.ravel(), v.ravel()], axis=1)
    und = cv2.undistortPoints(pix.reshape(-1, 1, 2), cam.K, cam.dist, P=cam.K).reshape(-1, 2)
    norm = np.linalg.solve(cam.K, np.column_stack([und, np.ones(len(pix))]).T)
    back, _ = cv2.projectPoints(norm.T.copy(), np.zeros(3), np.zeros(3), cam.K, cam.dist)
    folded = (np.hypot(*(back.reshape(-1, 2) - pix).T) > 0.5).reshape(h, w)
    assert folded.mean() > 0.01
    bed_rgb = np.asarray(sc.appearance.bed_rgb, np.float32)
    plate = np.clip(bed_rgb[::-1] * 255.0 + 0.5, 0, 255).astype(np.uint8)
    assert np.all(res.image[folded] == plate)
    tex = render_texture(lay, sc.offsets_mm, sc.appearance, np.random.default_rng(0), 4.0)
    ghosts = project_texture(tex, cam, bed_rgb)
    assert np.abs(ghosts[folded] - bed_rgb).max() > 0.1
