"""Configuration models.

All hardware-dependent values have conservative placeholders. Anything that is
not known for the Snapmaker U1 is flagged ``TO VERIFY ON HARDWARE`` both here and
in the YAML templates under ``config/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatternConfig(_Model):
    """Geometry of the printed calibration pattern (all values in mm)."""

    reference_tool: int = 0
    tools: list[int] = Field(default_factory=lambda: [1, 2, 3])
    include_control: bool = True
    """Add a T0-vs-T0 cell that measures the bias of the method itself."""

    pitch_mm: float = 1.2
    """Line pitch of the reference (T0) grating."""
    vernier_ratio: float = 0.95
    """Pitch of the measured tool grating = pitch_mm * vernier_ratio.

    1.0 gives two identical gratings (pure phase comparison). Values slightly
    below 1.0 turn the cell into a classic vernier that can also be read by eye,
    with a resolution of pitch_mm * (1 - vernier_ratio)."""
    grating_length_mm: float = 30.0
    """Extent of each grating along the measured axis."""
    line_length_mm: float = 5.0
    """Length of every grating line (= height of each of the three gratings of a cell)."""
    half_gap_mm: float = 0.8
    """Clearance between neighbouring gratings of a cell."""
    cell_spacing_mm: float = 6.0
    line_width_mm: float = 0.45
    layer_height_mm: float = 0.25

    fiducial_diameter_mm: float = 6.0
    orientation_dot_diameter_mm: float = 3.0
    fiducial_margin_mm: float = 5.0
    """Clearance between the cell area and the fiducial disks."""

    prime_lines: bool = True
    prime_line_length_mm: float = 40.0

    center_on_bed_mm: tuple[float, float] = (135.0, 135.0)
    """Where the pattern centre lands on the bed. TO VERIFY ON HARDWARE (bed size)."""
    rotation_deg: int = 0
    """Counter-clockwise rotation of the pattern on the bed: 0, 90, 180 or 270. Multiples
    of 90 keep every grating line parallel to a printer axis. With a camera on a side
    panel, 90 runs the long side of the pattern along the panel. TO VERIFY ON HARDWARE
    (camera position)."""

    @model_validator(mode="after")
    def _check(self) -> PatternConfig:
        if self.rotation_deg not in (0, 90, 180, 270):
            raise ValueError("rotation_deg must be 0, 90, 180 or 270")
        if self.reference_tool in self.tools:
            raise ValueError("reference_tool must not be listed in tools")
        if not 0.5 < self.vernier_ratio <= 1.0:
            raise ValueError("vernier_ratio must be in (0.5, 1.0]")
        if self.pitch_mm * self.vernier_ratio < 2.0 * self.line_width_mm:
            raise ValueError("pitch too small: adjacent lines would merge")
        return self


class FilamentConfig(_Model):
    nozzle_temp_c: float = 210.0
    color_hex: str = "#808080"
    """Only used for previews and the simulator."""


class PrinterConfig(_Model):
    """Printer description used by the G-code generator.

    Every default below is a placeholder. TO VERIFY ON HARDWARE.
    """

    name: str = "Snapmaker U1"
    bed_size_mm: tuple[float, float] = (270.0, 270.0)  # TO VERIFY ON HARDWARE
    tool_count: int = 4  # TO VERIFY ON HARDWARE
    nozzle_diameter_mm: float = 0.4  # TO VERIFY ON HARDWARE
    filament_diameter_mm: float = 1.75
    bed_temp_c: float = 60.0
    filaments: list[FilamentConfig] = Field(
        default_factory=lambda: [
            FilamentConfig(color_hex="#e8e8e8"),
            FilamentConfig(color_hex="#c83232"),
            FilamentConfig(color_hex="#2a5bd0"),
            FilamentConfig(color_hex="#e8b820"),
        ]
    )
    print_speed_mm_s: float = 20.0
    travel_speed_mm_s: float = 120.0
    z_hop_mm: float = 0.4
    retract_mm: float = 0.8
    retract_speed_mm_s: float = 30.0
    flow: float = 1.0

    gcode_start: str = (
        "; TO VERIFY ON HARDWARE: replace with the U1 start sequence\n"
        "; (homing, bed mesh, purge). Only standard Klipper commands are used here.\n"
        "M140 S{{ bed_temp }}\n"
        "G28\n"
        "M190 S{{ bed_temp }}\n"
    )
    gcode_tool_change: str = (
        "; TO VERIFY ON HARDWARE: U1 tool-change macro and its arguments.\n"
        "T{{ tool }}\n"
        "M109 S{{ nozzle_temp }}\n"
    )
    gcode_end: str = (
        "; TO VERIFY ON HARDWARE: replace with the U1 end sequence.\n"
        "G91\n"
        "G1 Z10 F600\n"
        "G90\n"
        "M104 S0\n"
        "M140 S0\n"
    )

    offset_correction_sign: Literal[-1, 1] = -1
    """How a measured error maps to a correction of the firmware offset.

    -1 means "the tool printed +0.1 mm too far, so subtract 0.1 mm from its offset".
    The real convention of the U1 firmware is unknown. TO VERIFY ON HARDWARE."""

    @model_validator(mode="after")
    def _check(self) -> PrinterConfig:
        if len(self.filaments) < self.tool_count:
            raise ValueError("one filament entry per tool is required")
        return self


class CameraConfig(_Model):
    """Chamber camera. Everything here is unknown for the U1. TO VERIFY ON HARDWARE."""

    snapshot_url: str = "/webcam/?action=snapshot"  # TO VERIFY ON HARDWARE
    resolution_px: tuple[int, int] = (1920, 1080)  # TO VERIFY ON HARDWARE
    camera_matrix: list[list[float]] | None = None
    """3x3 intrinsics from an OpenCV calibration. None = no undistortion."""
    dist_coeffs: list[float] | None = None


class MeasureConfig(_Model):
    """Quality gates of the measurement."""

    rect_px_per_mm: float = 20.0
    """Sampling density of the rectified cell images."""
    bands: int = 6
    """Each half is split along the lines into this many bands to estimate scatter."""
    end_trim_fraction: float = 0.15
    """Fraction of every line ignored at both ends (blobs, starts and stops)."""
    max_offset_mm: float = 0.5
    """Largest tool error the measurement will report. Larger errors are rejected:
    a grating shifted by more than half a pitch cannot be told apart from its
    neighbour line without extra information."""
    max_coarse_disagreement: float = 0.35
    """Envelope position vs phase reading, as a fraction of the pitch."""
    max_fiducial_reproj_px: float = 2.5
    min_modulation_snr: float = 2.0
    """Sanity gate: amplitude of the line signal over the fit residual."""
    min_contrast: float = 0.03
    """Minimum colour difference between filament and bed (RGB, 0..1)."""
    sigma_floor_mm: float = 0.006
    """Systematic uncertainty added in quadrature to the statistical one.

    Covers effects the statistics cannot see (e.g. lighting that shifts the apparent
    centre of a bead differently for different colours). The value is calibrated on
    synthetic data by the benchmark; it must be re-checked on real prints."""
    max_shading_sensitivity: float = 3.0
    """Upper limit of |line colour| / |line colour - bed colour|. Calibrated on the
    synthetic benchmark; TO VERIFY ON HARDWARE with the real chamber lighting."""
    max_shading_sensitivity_difference: float = 1.0
    """Upper limit of the sensitivity difference between Tn and T0. The lighting
    bias is differential: two filaments that react the same way cancel out.
    TO VERIFY ON HARDWARE."""
    max_sigma_mm: float = 0.02
    max_phase_vernier_disagreement_mm: float = 0.03
    max_control_bias_mm: float = 0.03


class AppConfig(_Model):
    pattern: PatternConfig = Field(default_factory=PatternConfig)
    printer: PrinterConfig = Field(default_factory=PrinterConfig)
    camera: CameraConfig = Field(default_factory=CameraConfig)
    measure: MeasureConfig = Field(default_factory=MeasureConfig)


def load_config(*paths: str | Path) -> AppConfig:
    """Merge one or more YAML files (later files win, section by section)."""
    merged: dict = {}
    for path in paths:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        for section, values in data.items():
            merged.setdefault(section, {}).update(values or {})
    return AppConfig.model_validate(merged)
