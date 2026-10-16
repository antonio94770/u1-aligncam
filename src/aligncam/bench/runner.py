"""Monte Carlo benchmark runner: simulate, measure, compare with ground truth."""

from __future__ import annotations

import csv
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from aligncam.bench.sweeps import random_scenario, sweep_cases
from aligncam.config import CameraConfig, MeasureConfig
from aligncam.geometry import build_layout
from aligncam.measure import measure_image
from aligncam.sim import simulate
from aligncam.sim.board import calibrate_synthetic
from aligncam.sim.camera import PinholeCamera
from aligncam.sim.scenario import Scenario, pattern_in_frame
from aligncam.sim.u1 import PLACEMENTS, random_side_pose

FIELDS = [
    "sweep",
    "value",
    "blur",
    "seed",
    "tool",
    "axis",
    "truth_mm",
    "estimate_mm",
    "error_mm",
    "sigma_mm",
    "accepted",
    "reasons",
    "gsd",
    "tilt",
    "bed",
    "colors",
    "view_deg",
    "cam_height_mm",
    "hfov_deg",
    "in_frame",
    "calib",
]


CALIB_VIEWS = 15


def recovered_calibration(
    cam: PinholeCamera, sc: Scenario, seed: int
) -> tuple[CameraConfig | None, str]:
    """Calibrate the scenario's full-sensor lens from synthetic board photos and express
    the result in the coordinates of the cropped scene image. Returns the camera config
    (None if no calibration could be computed) and the calibration status."""
    assert sc.pose is not None
    size = (sc.pose.width_px, sc.pose.height_px)
    centre = np.array(size, dtype=float) / 2.0
    K_full = cam.K.copy()
    K_full[:2, 2] = centre  # the crop shifts the principal point away from the centre
    rng = np.random.default_rng(seed + 7_000_001)
    res = calibrate_synthetic(K_full, cam.dist, size, rng, None, CALIB_VIEWS, sc.degradation)
    status = "ok" if res.ok else "rejected:" + "+".join(str(r) for r in res.reasons)
    if res.camera_matrix is None:
        return None, status
    K = res.camera_matrix.copy()
    K[:2, 2] += cam.K[:2, 2] - centre
    camera = CameraConfig(
        resolution_px=cam.size, camera_matrix=K.tolist(), dist_coeffs=res.dist_coeffs.tolist()
    )
    return camera, status


def run_case(args: tuple) -> list[dict]:
    sweep, value, fixed, seed = args
    names = fixed.pop("_names", None)
    placement = fixed.pop("placement", None)
    recalibrate = fixed.pop("recalibrate", False)
    rng = np.random.default_rng(seed)
    sc = random_scenario(rng, seed, **fixed)
    if placement is None:
        layout = build_layout()
    else:
        # Hypothetical U1 side camera: the pose replaces gsd/tilt (see aligncam.sim.u1).
        layout = build_layout(PLACEMENTS[int(placement)].pattern_config())
        sc = sc.model_copy(update={"pose": random_side_pose(rng)})
    res = simulate(sc, layout)
    img = res.image
    gsd = sc.gsd_mm_per_px if sc.pose is None else res.camera.gsd_at((0.0, 0.0))
    view = sc.tilt_deg if sc.pose is None else res.camera.view_angle_deg((0.0, 0.0))
    in_frame = sc.pose is None or pattern_in_frame(res.camera, layout, sc.pose)
    camera, calib = None, ""
    if recalibrate:
        camera, calib = recovered_calibration(res.camera, sc, seed)
    m = measure_image(img, layout, MeasureConfig(), camera)
    rows = []
    for t, to in m.tools.items():
        for i, axis in enumerate("xy"):
            truth = sc.offsets_mm[t][i]
            est = to.dx_mm if axis == "x" else to.dy_mm
            sig = to.sigma_x_mm if axis == "x" else to.sigma_y_mm
            rows.append(
                {
                    "sweep": sweep,
                    "value": value,
                    "blur": sc.degradation.blur_sigma_px,
                    "seed": seed,
                    "tool": t,
                    "axis": axis,
                    "truth_mm": truth,
                    "estimate_mm": "" if est is None else est,
                    "error_mm": "" if est is None else est - truth,
                    "sigma_mm": "" if sig is None else sig,
                    "accepted": int(to.status == "ok"),
                    "reasons": "|".join(str(r) for r in to.reasons),
                    "gsd": gsd,
                    "tilt": sc.tilt_deg,
                    "bed": "" if names is None else names[0],
                    "colors": "" if names is None else "/".join(str(n) for n in names[1:]),
                    "view_deg": view,
                    "cam_height_mm": "" if sc.pose is None else sc.pose.position_bed_mm[2],
                    "hfov_deg": "" if sc.pose is None else sc.pose.hfov_deg,
                    "in_frame": int(in_frame),
                    "calib": calib,
                }
            )
    return rows


def run_benchmark(
    out_dir: Path, n_per_point: int, workers: int | None = None, base_seed: int = 0
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = list(sweep_cases(n_per_point, base_seed))
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    path = out_dir / "results.csv"
    t0 = time.time()
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for i, rows in enumerate(pool.map(run_case, cases, chunksize=4), 1):
                writer.writerows(rows)
                if i % 200 == 0 or i == len(cases):
                    print(f"{i}/{len(cases)} images  {time.time() - t0:.0f}s", flush=True)
    return path
