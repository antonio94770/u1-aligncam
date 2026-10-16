import numpy as np
import pytest

from aligncam.measure import measure_image
from aligncam.measure.quality import Reason
from aligncam.sim import Scenario, simulate
from aligncam.sim.degrade import Degradation
from aligncam.sim.render import Appearance

IDEAL_APP = Appearance(
    bed_texture=0, width_jitter=0, wobble_mm=0, line_jitter_mm=0, start_blob=0, height_shading=0
)
IDEAL_DEG = Degradation(
    blur_sigma_px=0.5,
    illumination_gradient=0,
    vignetting=0,
    shot_noise=0,
    read_noise=0,
    jpeg_quality=None,
)


def _errors(sc, m):
    out = []
    for t, to in m.tools.items():
        gx, gy = sc.offsets_mm[t]
        out += [to.dx_mm - gx, to.dy_mm - gy]
    return np.array(out)


def test_ideal_image_is_exact(layout):
    sc = Scenario(seed=2, appearance=IDEAL_APP, degradation=IDEAL_DEG)
    m = measure_image(simulate(sc, layout).image, layout)
    assert m.status == "ok"
    assert np.abs(_errors(sc, m)).max() < 0.002


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_realistic_image_within_target(layout, seed):
    sc = Scenario(seed=seed, offsets_mm={1: (0.2, -0.1), 2: (-0.3, 0.05), 3: (0.0, 0.4)})
    m = measure_image(simulate(sc, layout).image, layout)
    assert m.status == "ok", m.to_dict()
    assert np.abs(_errors(sc, m)).max() < 0.03
    for to in m.tools.values():
        assert 0 < to.sigma_x_mm <= 0.02 and 0 < to.sigma_y_mm <= 0.02


def test_rotated_tilted_view(layout):
    sc = Scenario(seed=4, tilt_deg=35, roll_deg=25, gsd_mm_per_px=0.12)
    m = measure_image(simulate(sc, layout).image, layout)
    assert m.status == "ok", m.to_dict()
    assert np.abs(_errors(sc, m)).max() < 0.03


def test_blank_image_rejected(layout):
    img = np.full((600, 1000, 3), 40, np.uint8)
    m = measure_image(img, layout)
    assert m.status == "rejected"
    assert all(Reason.NO_FIDUCIALS in t.reasons for t in m.tools.values())


def test_invisible_tool_rejected(layout):
    sc = Scenario(seed=5, appearance=Appearance(contrast={2: 0.0}))
    m = measure_image(simulate(sc, layout).image, layout)
    assert m.tools[2].status == "rejected"
    assert m.tools[1].status == "ok" and m.tools[3].status == "ok"
    assert m.status == "partial"


def test_too_coarse_camera_rejected(layout):
    sc = Scenario(seed=6, gsd_mm_per_px=0.55)
    m = measure_image(simulate(sc, layout).image, layout)
    assert m.status != "ok"


def test_out_of_range_offset_never_silently_wrong(layout):
    sc = Scenario(seed=7, offsets_mm={1: (0.8, 0.0), 2: (0.0, 0.0), 3: (0.0, 0.0)})
    m = measure_image(simulate(sc, layout).image, layout)
    t1 = m.tools[1]
    assert t1.status == "rejected" or abs(t1.dx_mm - 0.8) < 0.03


def test_correction_sign(layout):
    m = measure_image(simulate(Scenario(seed=8), layout).image, layout)
    t = m.tools[1]
    assert t.correction(-1) == (-t.dx_mm, -t.dy_mm)


def test_overlay_and_dict(layout):
    from aligncam.measure.overlay import draw_overlay

    r = simulate(Scenario(seed=9), layout)
    m = measure_image(r.image, layout)
    assert set(m.to_dict()["tools"]) == {"T1", "T2", "T3"}
    assert draw_overlay(r.image, layout, m).shape[0] == 2 * r.image.shape[0]


def test_polarity_mismatch_rejected(layout):
    # White T0 and black T1 on a mid-grey plate: one lighter, one darker.
    app = Appearance(
        bed_rgb=(0.4, 0.4, 0.4),
        tool_rgb={
            0: (0.9, 0.9, 0.9),
            1: (0.05, 0.05, 0.05),
            2: (0.9, 0.2, 0.2),
            3: (0.9, 0.8, 0.2),
        },
    )
    m = measure_image(simulate(Scenario(seed=10, appearance=app), layout).image, layout)
    assert m.tools[1].status == "rejected"
    assert Reason.POLARITY_MISMATCH in m.tools[1].reasons


def test_reference_unwrap_survives_uneven_light(layout):
    deg = Degradation(illumination_gradient=0.4, vignetting=0.3)
    sc = Scenario(seed=11, degradation=deg)
    m = measure_image(simulate(sc, layout).image, layout)
    for c in m.cells:
        assert c.reference_split_mm is not None and abs(c.reference_split_mm) < 0.1


def test_fiducials_found_when_bed_is_not_the_dominant_colour(layout):
    """The plate is a small part of a frame dominated by something else (a light
    chamber wall here; a dark room around a screen in the iPad test): the global
    detector takes that as the bed; the local fallback must still find the disks."""
    from aligncam.measure.fiducials import contrast_map, find_blobs, find_fiducials

    sc = Scenario(seed=3, gsd_mm_per_px=0.12, appearance=IDEAL_APP, degradation=IDEAL_DEG)
    res = simulate(sc, layout)
    h, w = res.image.shape[:2]
    framed = np.full((3 * h, 3 * w, 3), 200, np.uint8)
    framed[h : 2 * h, w : 2 * w] = res.image
    cmap = contrast_map(framed)
    assert len(find_blobs(cmap)) < 5  # the global detector sees one big blob
    fit = find_fiducials(framed, layout)
    assert fit is not None
    truth = res.camera.project(layout.fiducial_points()) + np.array([w, h])
    assert np.abs(fit.image_points - truth).max() < 1.5  # disk radius ~23 px
    m = measure_image(framed, layout)
    assert m.status == "ok"
    assert max(abs(e) for e in _errors(sc, m)) < 0.01
