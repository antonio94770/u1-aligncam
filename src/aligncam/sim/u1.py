"""Hypothetical poses of the Snapmaker U1 chamber camera.

Public facts (docs/u1-camera.md): a 1920 x 1080 camera on the right inner side
panel. Everything else is unknown, so every pose here is a draw from a range of
plausible values, all TO VERIFY ON HARDWARE.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from aligncam.config import PatternConfig
from aligncam.sim.scenario import CameraPose

BED_MM = 270.0
"""Published build volume; the usable plate area is TO VERIFY ON HARDWARE."""

HEIGHT_RANGE_MM = (50.0, 200.0)
"""Camera height above the bed surface at photo time. TO VERIFY ON HARDWARE."""
CLEARANCE_RANGE_MM = (20.0, 50.0)
"""Horizontal distance from the camera to the right edge of the bed. TO VERIFY ON HARDWARE."""
HFOV_RANGE_DEG = (80.0, 120.0)
"""Horizontal field of view ("wide-angle" per Snapmaker). TO VERIFY ON HARDWARE."""
PANEL_Y_RANGE_MM = (60.0, 210.0)
"""Where along the right panel (bed Y) the camera sits. TO VERIFY ON HARDWARE."""


def side_camera_pose(
    height_mm: float,
    clearance_mm: float,
    hfov_deg: float,
    panel_y_mm: float = BED_MM / 2,
    aim_bed_mm: tuple[float, float] = (BED_MM / 2, BED_MM / 2),
) -> CameraPose:
    """Camera on the right side panel, aimed at ``aim_bed_mm`` (default: bed centre)."""
    return CameraPose(
        position_bed_mm=(BED_MM + clearance_mm, panel_y_mm, height_mm),
        aim_bed_mm=aim_bed_mm,
        hfov_deg=hfov_deg,
    )


def random_side_pose(rng: np.random.Generator) -> CameraPose:
    return side_camera_pose(
        height_mm=float(rng.uniform(*HEIGHT_RANGE_MM)),
        clearance_mm=float(rng.uniform(*CLEARANCE_RANGE_MM)),
        hfov_deg=float(rng.uniform(*HFOV_RANGE_DEG)),
        panel_y_mm=float(rng.uniform(*PANEL_Y_RANGE_MM)),
    )


@dataclass(frozen=True)
class Placement:
    """Where the pattern is printed, as benchmarked for the U1 side camera."""

    name: str
    center_on_bed_mm: tuple[float, float]
    rotation_deg: int

    def pattern_config(self, base: PatternConfig | None = None) -> PatternConfig:
        base = base or PatternConfig()
        return base.model_copy(
            update={"center_on_bed_mm": self.center_on_bed_mm, "rotation_deg": self.rotation_deg}
        )


PLACEMENTS = (
    Placement("bed centre, 0 deg", (135.0, 135.0), 0),
    Placement("bed centre, 90 deg", (135.0, 135.0), 90),
    Placement("70 mm from camera edge, 90 deg", (200.0, 135.0), 90),
    Placement("50 mm from camera edge, 90 deg", (220.0, 135.0), 90),
)
