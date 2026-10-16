"""Synthetic scenes with known ground truth."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, Field

from aligncam.config import PatternConfig
from aligncam.geometry import Layout, build_layout
from aligncam.sim.camera import PinholeCamera, camera_for_gsd, camera_from_pose
from aligncam.sim.degrade import Degradation, apply_degradation, project_texture
from aligncam.sim.render import Appearance, render_texture


class CameraPose(BaseModel):
    """A physical camera in bed coordinates (mm, z above the bed surface)."""

    position_bed_mm: tuple[float, float, float]
    aim_bed_mm: tuple[float, float]
    hfov_deg: float = 100.0
    width_px: int = 1920
    height_px: int = 1080


class Scenario(BaseModel):
    """Everything needed to reproduce one synthetic photo."""

    seed: int = 0
    offsets_mm: dict[int, tuple[float, float]] = Field(
        default_factory=lambda: {1: (0.05, -0.03), 2: (-0.08, 0.02), 3: (0.0, 0.11)}
    )
    """Ground-truth XY error of each tool relative to T0."""
    gsd_mm_per_px: float = 0.15
    tilt_deg: float = 20.0
    azimuth_deg: float = -90.0
    roll_deg: float = 0.0
    camera_distance_mm: float = 250.0
    """Distance from the camera to the pattern centre. TO VERIFY ON HARDWARE."""
    k1: float = 0.0
    """Radial distortion, OpenCV k1 on normalised coordinates, optical axis through the
    pattern centre. Uncalibrated wide-angle webcams are typically around -0.1 .. -0.3."""
    pose: CameraPose | None = None
    """Physical camera pose. When set, it replaces gsd/tilt/azimuth/distance: the GSD
    and viewing angle follow from the pose, the FOV and where the layout puts the
    pattern on the bed (``center_on_bed_mm``, ``rotation_deg``)."""
    texture_px_per_mm: float | None = None
    """None = choose automatically from the GSD."""
    appearance: Appearance = Field(default_factory=Appearance)
    degradation: Degradation = Field(default_factory=Degradation)


@dataclass
class SimResult:
    image: np.ndarray
    """BGR uint8, as a webcam snapshot would be decoded by OpenCV."""
    scenario: Scenario
    camera: PinholeCamera
    layout: Layout

    @property
    def H_img_from_mm(self) -> np.ndarray:
        return self.camera.homography()

    def truth(self) -> dict:
        return {
            "offsets_mm": {f"T{k}": list(v) for k, v in self.scenario.offsets_mm.items()},
            "fiducials_px": self.camera.project(self.layout.fiducial_points()).tolist(),
            "gsd_mm_per_px": self.camera.gsd_at((0.0, 0.0)),
            "H_img_from_mm": self.H_img_from_mm.tolist(),
            "scenario": self.scenario.model_dump(mode="json"),
        }

    def save(self, stem: str | Path) -> tuple[Path, Path]:
        stem = Path(stem)
        stem.parent.mkdir(parents=True, exist_ok=True)
        img_path = stem.with_suffix(".png")
        json_path = stem.with_suffix(".json")
        cv2.imwrite(str(img_path), self.image)
        json_path.write_text(json.dumps(self.truth(), indent=2), encoding="utf-8")
        return img_path, json_path


def auto_texture_density(gsd_mm_per_px: float) -> float:
    return float(np.clip(2.5 / gsd_mm_per_px, 8.0, 24.0))


def simulate(scenario: Scenario, layout: Layout | None = None) -> SimResult:
    """Render the scenario's photo.

    Pixels beyond the fold radius of the k1-only lens model are rendered as plate
    colour (``project_texture(mask_invalid=True)``). With k1 < 0 the model's image
    radius r(1 + k1 r^2) peaks and turns back at r^2 = -1/(3 k1); a wide FOV puts
    image corners past that peak (with the U1 side camera poses, about a third of
    the uncorrected cases at the two placements near the camera, and the corners of
    the k1 = -0.4 distortion sweep). Those pixels have no pre-image, and
    ``cv2.undistortPoints`` returns a wrong one that paints ghost copies of the
    scene. Masking leaves every unfolded image unchanged (in the n = 60 benchmark
    it changed no measurement at all).

    k1 is deliberately not limited to the fold-free range of the FOV instead: that
    bound (k1 >= -4 / (27 r_corner^2), about -0.04 at HFOV 120 deg) is a limit of the
    one-coefficient model, not of real wide-angle lenses, and would tie the drawn
    distortion to the FOV exactly where strong barrel distortion is expected. The
    cost is that a few pose draws fold onto the pattern itself (5 of the 240
    uncorrected U1 scenes); part of the pattern is then missing and the measurement
    nearly always rejects, so those cases count as failures, a pessimistic
    artefact of the lens model.
    """
    layout = layout or build_layout(PatternConfig())
    rng = np.random.default_rng(scenario.seed)
    app = scenario.appearance
    cam = scene_camera(scenario, layout)
    gsd = scenario.gsd_mm_per_px if scenario.pose is None else cam.gsd_at((0.0, 0.0))
    density = scenario.texture_px_per_mm or auto_texture_density(gsd)
    offsets = {int(k): tuple(v) for k, v in scenario.offsets_mm.items()}
    tex = render_texture(layout, offsets, app, rng, px_per_mm=density)
    linear = project_texture(tex, cam, np.asarray(app.bed_rgb, np.float32), mask_invalid=True)
    image = apply_degradation(linear, scenario.degradation, rng)
    return SimResult(image, scenario, cam, layout)


def scene_camera(scenario: Scenario, layout: Layout) -> PinholeCamera:
    """The scenario's camera in the pattern frame, cropped around the pattern."""
    x0, y0, x1, y1 = layout.bbox()
    roi = (x0 - 6, y0 - 6, x1 + 6, y1 + 6)
    if scenario.pose is None:
        return camera_for_gsd(
            scenario.gsd_mm_per_px,
            roi,
            tilt_deg=scenario.tilt_deg,
            azimuth_deg=scenario.azimuth_deg,
            roll_deg=scenario.roll_deg,
            k1=scenario.k1,
            distance_mm=scenario.camera_distance_mm,
        )
    pose = scenario.pose
    centre = np.asarray(layout.config.center_on_bed_mm, dtype=float)
    rot = layout.rotation()
    # Bed -> pattern frame: inverse of Layout.to_bed (row vectors, so R^-T = R).
    pos_xy = (np.asarray(pose.position_bed_mm[:2]) - centre) @ rot
    aim_xy = (np.asarray(pose.aim_bed_mm) - centre) @ rot
    return camera_from_pose(
        (float(pos_xy[0]), float(pos_xy[1]), float(pose.position_bed_mm[2])),
        (float(aim_xy[0]), float(aim_xy[1])),
        pose.hfov_deg,
        roi,
        width_px=pose.width_px,
        height_px=pose.height_px,
        roll_deg=scenario.roll_deg,
        k1=scenario.k1,
    )


def pattern_in_frame(camera: PinholeCamera, layout: Layout, pose: CameraPose) -> bool:
    """Whether the whole printed pattern falls inside the sensor of a posed camera."""
    x0, y0, x1, y1 = layout.bbox()
    uv = camera.project(np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])) - camera.K[:2, 2]
    half = np.array([pose.width_px, pose.height_px], dtype=float) / 2.0
    return bool(np.all(np.abs(uv) <= half))


def random_offsets(
    rng: np.random.Generator, tools: list[int], max_abs_mm: float
) -> dict[int, tuple[float, float]]:
    return {
        t: (
            float(rng.uniform(-max_abs_mm, max_abs_mm)),
            float(rng.uniform(-max_abs_mm, max_abs_mm)),
        )
        for t in tools
    }
