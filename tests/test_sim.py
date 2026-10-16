import numpy as np

from aligncam.sim import Scenario, simulate
from aligncam.sim.camera import camera_for_gsd, distance_for_gsd, gsd_for_camera


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
