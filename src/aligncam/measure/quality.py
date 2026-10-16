"""Reason codes for rejected or suspicious measurements."""

from __future__ import annotations

from enum import StrEnum


class Reason(StrEnum):
    NO_FIDUCIALS = "no_fiducials"
    """The four corner disks and the orientation dot were not found."""
    FIDUCIAL_REPROJECTION = "fiducial_reprojection"
    """The fiducials do not fit a plane homography well (wrong blob, warped plate)."""
    OUTSIDE_IMAGE = "outside_image"
    """Part of a cell projects outside the image."""
    LOW_CONTRAST = "low_contrast"
    """Filament and bed colours are too similar."""
    POLARITY_MISMATCH = "polarity_mismatch"
    """One filament is lighter than the plate and the other darker. Directional
    lighting then shifts the apparent bead centres in opposite directions, a bias
    that no internal check can see. Use filaments that are all lighter (or all
    darker) than the plate."""
    SHADING_SENSITIVE = "shading_sensitive"
    """The filament colour differs little from the plate compared with its own
    brightness, so bead shading from directional light can move the apparent
    line centre by more than the target accuracy."""
    LOW_SNR = "low_snr"
    """The periodic line signal is weak compared with the residual."""
    HIGH_UNCERTAINTY = "high_uncertainty"
    """The estimated standard deviation exceeds the configured limit."""
    PHASE_VERNIER_MISMATCH = "phase_vernier_mismatch"
    """The phase estimate and the line-by-line vernier reading disagree."""
    COARSE_MISMATCH = "coarse_mismatch"
    """The position of the whole grating (envelope) disagrees with the phase
    reading by a large fraction of a pitch: the period cannot be trusted."""
    OUT_OF_RANGE = "out_of_range"
    """The offset is larger than the unambiguous measurement range."""
    CONTROL_BIAS = "control_bias"
    """The T0-vs-T0 control cell is not zero: the whole measurement is suspect."""
    MISSING_CELL = "missing_cell"
    """No usable cell for this axis."""
