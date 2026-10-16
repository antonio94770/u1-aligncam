"""Benchmark scenarios: a randomised baseline and one-factor-at-a-time sweeps.

Every case draws its nuisance parameters (print imperfections, lighting, pose,
compression) at random, so a sweep point averages over realistic conditions
instead of testing a single lucky configuration. Only the swept parameter is fixed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from aligncam.sim.degrade import Degradation
from aligncam.sim.render import Appearance
from aligncam.sim.scenario import Scenario, random_offsets
from aligncam.sim.u1 import PLACEMENTS

TOOLS = [1, 2, 3]
MAX_OFFSET_MM = 0.3
"""Range of the random ground-truth tool errors (uniform, per axis)."""

# Plate colours (linear-ish RGB). The real U1 plate is TO VERIFY ON HARDWARE.
BEDS = {
    "dark_pei": (0.17, 0.17, 0.18),
    "gold_pei": (0.55, 0.40, 0.20),
}
FILAMENTS = {
    "white": (0.91, 0.91, 0.91),
    "black": (0.06, 0.06, 0.07),
    "grey": (0.45, 0.45, 0.46),
    "red": (0.78, 0.20, 0.20),
    "blue": (0.16, 0.36, 0.82),
    "yellow": (0.91, 0.72, 0.13),
    "green": (0.15, 0.62, 0.25),
    "orange": (0.95, 0.45, 0.10),
}


def random_scenario(rng: np.random.Generator, seed: int, **fixed) -> Scenario:
    """Baseline scenario with randomised nuisance parameters.

    Keyword arguments override the corresponding random draw. Accepted keys:
    gsd, blur, read_noise, contrast, tilt, k1, lighting, bed, colors.
    """
    light = rng.normal(size=2)
    app = Appearance(
        bed_rgb=BEDS[fixed.get("bed", "dark_pei")],
        bed_texture=float(rng.uniform(0.02, 0.1)),
        tool_rgb=fixed.get("colors", Appearance().tool_rgb),
        contrast={t: float(fixed["contrast"]) for t in range(4)} if "contrast" in fixed else {},
        width_jitter=float(rng.uniform(0.03, 0.1)),
        width_scale={t: float(rng.uniform(0.85, 1.15)) for t in range(4)},
        wobble_mm=float(rng.uniform(0.005, 0.02)),
        line_jitter_mm=float(rng.uniform(0.005, 0.02)),
        start_blob=float(rng.uniform(0.0, 0.5)),
        height_shading=float(fixed.get("lighting", rng.uniform(0.0, 0.3))),
        light_dir=(float(light[0]), float(light[1])),
    )
    deg = Degradation(
        blur_sigma_px=float(fixed.get("blur", rng.uniform(0.5, 1.2))),
        illumination_gradient=float(rng.uniform(0.0, 0.3)),
        vignetting=float(rng.uniform(0.0, 0.3)),
        exposure=float(rng.uniform(0.7, 0.95)),
        shot_noise=float(rng.uniform(0.005, 0.02)),
        read_noise=float(fixed.get("read_noise", rng.uniform(0.003, 0.01))),
        jpeg_quality=int(rng.integers(70, 96)),
    )
    return Scenario(
        seed=seed,
        offsets_mm=random_offsets(rng, TOOLS, MAX_OFFSET_MM),
        gsd_mm_per_px=float(fixed.get("gsd", 0.15)),
        tilt_deg=float(fixed.get("tilt", rng.uniform(0.0, 35.0))),
        azimuth_deg=float(rng.uniform(-120.0, -60.0)),
        roll_deg=float(rng.uniform(-10.0, 10.0)),
        k1=float(fixed.get("k1", rng.uniform(-0.15, 0.0))),
        appearance=app,
        degradation=deg,
    )


@dataclass(frozen=True)
class Sweep:
    name: str
    param: str
    """Keyword passed to :func:`random_scenario`."""
    label: str
    values: tuple
    fixed: dict | None = None


SWEEPS: dict[str, Sweep] = {
    s.name: s
    for s in [
        Sweep(
            "gsd",
            "gsd",
            "Ground sampling distance (mm/px)",
            (0.05, 0.075, 0.1, 0.125, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5),
        ),
        Sweep("blur", "blur", "Blur sigma (px)", (0.3, 0.6, 1.0, 1.5, 2.0, 2.5, 3.0)),
        Sweep(
            "noise",
            "read_noise",
            "Sensor noise std (fraction of full scale)",
            (0.002, 0.005, 0.01, 0.02, 0.03, 0.05),
        ),
        Sweep(
            "contrast",
            "contrast",
            "Filament/bed contrast (1 = nominal colours)",
            (0.03, 0.05, 0.08, 0.12, 0.2, 0.35, 0.6, 1.0),
        ),
        Sweep("tilt", "tilt", "Camera tilt from vertical (deg)", (0, 15, 30, 45, 55, 65)),
        Sweep(
            "distortion",
            "k1",
            "Uncorrected lens distortion k1 (normalised)",
            (0.0, -0.05, -0.1, -0.2, -0.3, -0.4),
        ),
        Sweep(
            "lighting",
            "lighting",
            "Lighting directionality (bead shading strength)",
            (0.0, 0.1, 0.2, 0.3, 0.4, 0.5),
        ),
    ]
}

GRID_GSD = (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4)
GRID_BLUR = (0.5, 1.0, 1.5, 2.0, 3.0)


def sweep_cases(n_per_point: int, base_seed: int = 0):
    """Yield (sweep name, value, scenario factory args) for every case."""
    seed = base_seed
    for sweep in SWEEPS.values():
        for v in sweep.values:
            for _ in range(n_per_point):
                yield sweep.name, float(v), {sweep.param: v}, seed
                seed += 1
    for g in GRID_GSD:
        for b in GRID_BLUR:
            for _ in range(max(n_per_point // 2, 1)):
                yield "grid", float(g), {"gsd": g, "blur": b}, seed
                seed += 1
    # Hypothetical U1 side camera, with uncorrected distortion and with a calibrated
    # lens (k1 = 0 stands for a perfect undistortion).
    for name, extra in (("u1", {}), ("u1_cal", {"k1": 0.0})):
        for i in range(len(PLACEMENTS)):
            for _ in range(n_per_point):
                yield name, float(i), {"placement": i, **extra}, seed
                seed += 1
    colors_rng = np.random.default_rng(base_seed + 99)
    names = list(FILAMENTS)
    for _ in range(n_per_point * 4):
        bed = str(colors_rng.choice(list(BEDS)))
        picks = colors_rng.choice(names, size=4, replace=False)
        colors = {t: FILAMENTS[str(c)] for t, c in enumerate(picks)}
        yield "colors", 0.0, {"bed": bed, "colors": colors, "_names": [bed, *picks]}, seed
        seed += 1


ScenarioFactory = Callable[[np.random.Generator, int], Scenario]
