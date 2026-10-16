"""Pattern layout in millimetres: the single source of truth.

The G-code generator, the image simulator and the measurement all build on the
:class:`Layout` returned by :func:`build_layout`.

Pattern frame: origin at the pattern centre, +X right, +Y towards the back of the
bed. A *cell* measures one axis of one tool. It holds three gratings that abut
without overlapping, like the scales of a caliper: reference | measured |
reference. The reference tool prints both outer gratings, the measured tool the
middle one.

* X cell: vertical lines (the grating varies along X), stacked along Y.
* Y cell: horizontal lines (the grating varies along Y), stacked along X.

The reference position is the mean of the two outer gratings, which sit
symmetrically around the measured one. Any smooth error field (lens distortion,
an imperfect homography, a slightly wrong scale) that varies linearly across the
cell cancels in that mean.

The line with index k = 0 of every grating sits on the cell centre, so a
measured tool with zero error has its central line exactly in line with the
reference central lines (the vernier "zero").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from aligncam.config import PatternConfig

Axis = Literal["x", "y"]
XY = tuple[float, float]


@dataclass(frozen=True)
class Grating:
    tool: int
    axis: Axis
    """Measured axis: the line centres vary along this axis."""
    center: XY
    pitch: float
    n_lines: int
    line_length: float

    @property
    def indices(self) -> np.ndarray:
        half = (self.n_lines - 1) // 2
        return np.arange(-half, half + 1)

    def positions(self) -> np.ndarray:
        """Absolute coordinate of every line centre along the measured axis (mm)."""
        c = self.center[0] if self.axis == "x" else self.center[1]
        return c + self.indices * self.pitch

    def segments(self) -> list[tuple[XY, XY]]:
        """Start and end point of every line, alternating direction (serpentine)."""
        segs = []
        h = self.line_length / 2.0
        for i, p in enumerate(self.positions()):
            if self.axis == "x":
                a, b = (p, self.center[1] - h), (p, self.center[1] + h)
            else:
                a, b = (self.center[0] - h, p), (self.center[0] + h, p)
            segs.append((a, b) if i % 2 == 0 else (b, a))
        return segs

    def bbox(self, line_width: float) -> tuple[float, float, float, float]:
        pos = self.positions()
        h = self.line_length / 2.0
        w = line_width / 2.0
        if self.axis == "x":
            return (pos[0] - w, self.center[1] - h, pos[-1] + w, self.center[1] + h)
        return (self.center[0] - h, pos[0] - w, self.center[0] + h, pos[-1] + w)


@dataclass(frozen=True)
class Cell:
    tool: int
    """Tool being measured (equal to the reference tool for the control cell)."""
    axis: Axis
    center: XY
    references: tuple[Grating, Grating]
    """The two outer reference gratings (before and after the measured one)."""
    measured: Grating

    @property
    def reference(self) -> Grating:
        return self.references[0]

    @property
    def gratings(self) -> tuple[Grating, ...]:
        return (self.references[0], self.measured, self.references[1])

    @property
    def name(self) -> str:
        return f"T{self.tool}-{self.axis}"

    @property
    def is_control(self) -> bool:
        return self.tool == self.reference.tool

    def bbox(self, line_width: float) -> tuple[float, float, float, float]:
        boxes = np.array([g.bbox(line_width) for g in self.gratings])
        return (
            float(boxes[:, 0].min()),
            float(boxes[:, 1].min()),
            float(boxes[:, 2].max()),
            float(boxes[:, 3].max()),
        )


@dataclass(frozen=True)
class Disk:
    tool: int
    center: XY
    diameter: float
    role: Literal["fiducial", "orientation"]


@dataclass(frozen=True)
class Path:
    """One continuous extrusion move sequence for a single tool."""

    tool: int
    points: tuple[XY, ...]
    role: Literal["grating", "fiducial", "orientation", "prime"]


@dataclass
class Layout:
    config: PatternConfig
    cells: list[Cell]
    fiducials: list[Disk]
    """Corner disks in the order top-left, top-right, bottom-right, bottom-left."""
    orientation_dot: Disk
    prime_lines: list[Path] = field(default_factory=list)

    @property
    def print_order(self) -> list[int]:
        return [self.config.reference_tool, *self.config.tools]

    @property
    def line_width(self) -> float:
        return self.config.line_width_mm

    def cells_for(self, tool: int) -> list[Cell]:
        return [c for c in self.cells if c.tool == tool]

    def cell_area(self) -> tuple[float, float, float, float]:
        boxes = np.array([c.bbox(self.line_width) for c in self.cells])
        return (
            float(boxes[:, 0].min()),
            float(boxes[:, 1].min()),
            float(boxes[:, 2].max()),
            float(boxes[:, 3].max()),
        )

    def bbox(self) -> tuple[float, float, float, float]:
        """Bounding box of everything that gets printed (mm, pattern frame)."""
        x0, y0, x1, y1 = self.cell_area()
        pts = [(x0, y0), (x1, y1)]
        for d in [*self.fiducials, self.orientation_dot]:
            r = d.diameter / 2.0
            pts += [(d.center[0] - r, d.center[1] - r), (d.center[0] + r, d.center[1] + r)]
        for p in self.prime_lines:
            pts += list(p.points)
        arr = np.array(pts)
        w = self.line_width / 2.0
        return (arr[:, 0].min() - w, arr[:, 1].min() - w, arr[:, 0].max() + w, arr[:, 1].max() + w)

    def fiducial_points(self) -> np.ndarray:
        """Corner fiducial centres plus the orientation dot, shape (5, 2)."""
        pts = [d.center for d in self.fiducials] + [self.orientation_dot.center]
        return np.array(pts, dtype=float)

    def rotation(self) -> np.ndarray:
        """2x2 rotation from pattern axes to bed (printer) axes."""
        return rotation_matrix(self.config.rotation_deg)

    def to_bed(self, xy: np.ndarray | XY) -> np.ndarray:
        """Pattern-frame points (..., 2) to bed coordinates."""
        return self.vector_to_bed(xy) + np.asarray(self.config.center_on_bed_mm)

    def vector_to_bed(self, v: np.ndarray | XY) -> np.ndarray:
        """Pattern-frame displacements (..., 2), e.g. a measured tool offset, in bed axes."""
        return np.asarray(v, dtype=float) @ self.rotation().T

    def bed_bbox(self) -> tuple[float, float, float, float]:
        """Bounding box of everything that gets printed, in bed coordinates."""
        x0, y0, x1, y1 = self.bbox()
        c = self.to_bed(np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)]))
        return (c[:, 0].min(), c[:, 1].min(), c[:, 0].max(), c[:, 1].max())

    def toolpaths(self, tool: int) -> list[Path]:
        """Every extrusion path of one tool, in print order."""
        paths: list[Path] = [p for p in self.prime_lines if p.tool == tool]
        for disk in [*self.fiducials, self.orientation_dot]:
            if disk.tool == tool:
                paths += _disk_paths(disk, self.line_width)
        for cell in self.cells:
            for grating in cell.gratings:
                if grating.tool == tool:
                    paths += [Path(tool, seg, "grating") for seg in grating.segments()]
        return paths


def rotation_matrix(deg: float) -> np.ndarray:
    """Counter-clockwise 2x2 rotation, exact for multiples of 90 degrees."""
    c, s = round(math.cos(math.radians(deg)), 12), round(math.sin(math.radians(deg)), 12)
    return np.array([[c, -s], [s, c]])


def _n_lines(length: float, pitch: float) -> int:
    return 2 * int(math.floor(length / 2.0 / pitch + 1e-9)) + 1


def _cell(cfg: PatternConfig, tool: int, axis: Axis, center: XY) -> Cell:
    ref_pitch = cfg.pitch_mm
    meas_pitch = cfg.pitch_mm * cfg.vernier_ratio
    step = cfg.half_gap_mm + cfg.line_length_mm
    cx, cy = center
    n_ref = _n_lines(cfg.grating_length_mm, ref_pitch)
    n_meas = _n_lines(cfg.grating_length_mm, meas_pitch)
    if axis == "x":
        ref_centers = [(cx, cy + step), (cx, cy - step)]
    else:
        ref_centers = [(cx - step, cy), (cx + step, cy)]
    refs = tuple(
        Grating(cfg.reference_tool, axis, c, ref_pitch, n_ref, cfg.line_length_mm)
        for c in ref_centers
    )
    meas = Grating(tool, axis, (cx, cy), meas_pitch, n_meas, cfg.line_length_mm)
    return Cell(tool, axis, center, refs, meas)  # type: ignore[arg-type]


def _disk_paths(disk: Disk, line_width: float) -> list[Path]:
    """Fill a disk with concentric circles, outermost first."""
    paths = []
    r = disk.diameter / 2.0 - line_width / 2.0
    step = 0.9 * line_width
    while r > line_width / 2.0:
        n = max(16, int(2 * math.pi * r / 0.5))
        ang = np.linspace(0.0, 2.0 * math.pi, n + 1)
        pts = tuple(
            (disk.center[0] + r * math.cos(a), disk.center[1] + r * math.sin(a)) for a in ang
        )
        paths.append(Path(disk.tool, pts, disk.role))
        r -= step
    cx, cy = disk.center
    paths.append(Path(disk.tool, ((cx - line_width / 2, cy), (cx + line_width / 2, cy)), disk.role))
    return paths


def build_layout(cfg: PatternConfig | None = None) -> Layout:
    cfg = cfg or PatternConfig()
    measured = list(cfg.tools) + ([cfg.reference_tool] if cfg.include_control else [])
    cols = len(measured)
    col_pitch = cfg.grating_length_mm + cfg.cell_spacing_mm
    cell_h = 3 * cfg.line_length_mm + 2 * cfg.half_gap_mm
    row_x_h = cell_h  # X cells are wide and short
    row_y_h = cfg.grating_length_mm  # Y cells are narrow and tall
    total_h = row_x_h + cfg.cell_spacing_mm + row_y_h
    y_row_x = total_h / 2.0 - row_x_h / 2.0
    y_row_y = -total_h / 2.0 + row_y_h / 2.0

    cells = []
    for i, tool in enumerate(measured):
        cx = (i - (cols - 1) / 2.0) * col_pitch
        cells.append(_cell(cfg, tool, "x", (cx, y_row_x)))
        cells.append(_cell(cfg, tool, "y", (cx, y_row_y)))

    w = cfg.line_width_mm
    boxes = np.array([c.bbox(w) for c in cells])
    x0, y0 = boxes[:, 0].min(), boxes[:, 1].min()
    x1, y1 = boxes[:, 2].max(), boxes[:, 3].max()
    r = cfg.fiducial_diameter_mm / 2.0
    m = cfg.fiducial_margin_mm + r
    ref = cfg.reference_tool
    corners = [(x0 - m, y1 + m), (x1 + m, y1 + m), (x1 + m, y0 - m), (x0 - m, y0 - m)]
    fiducials = [Disk(ref, c, cfg.fiducial_diameter_mm, "fiducial") for c in corners]
    rd = cfg.orientation_dot_diameter_mm / 2.0
    dot = Disk(
        ref,
        (corners[0][0] + r + 2.0 + rd, corners[0][1]),
        cfg.orientation_dot_diameter_mm,
        "orientation",
    )

    prime = []
    if cfg.prime_lines:
        half = cfg.prime_line_length_mm / 2.0
        for k, tool in enumerate([ref, *cfg.tools]):
            y = corners[3][1] - r - 4.0 - 2.0 * k
            prime.append(Path(tool, ((-half, y), (half, y)), "prime"))

    return Layout(cfg, cells, fiducials, dot, prime)
