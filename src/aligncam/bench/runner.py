"""Monte Carlo benchmark runner: simulate, measure, compare with ground truth."""

from __future__ import annotations

import csv
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from aligncam.bench.sweeps import random_scenario, sweep_cases
from aligncam.config import MeasureConfig
from aligncam.geometry import build_layout
from aligncam.measure import measure_image
from aligncam.sim import simulate

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
]


def run_case(args: tuple) -> list[dict]:
    sweep, value, fixed, seed = args
    names = fixed.pop("_names", None)
    rng = np.random.default_rng(seed)
    sc = random_scenario(rng, seed, **fixed)
    layout = build_layout()
    img = simulate(sc, layout).image
    m = measure_image(img, layout, MeasureConfig())
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
                    "gsd": sc.gsd_mm_per_px,
                    "tilt": sc.tilt_deg,
                    "bed": "" if names is None else names[0],
                    "colors": "" if names is None else "/".join(str(n) for n in names[1:]),
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
