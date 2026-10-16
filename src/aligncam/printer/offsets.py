"""Turning a measurement into a proposed offset change, and the writer interface.

Safety model:

1. A :class:`OffsetProposal` is built only from tools whose measurement was
   accepted. Corrections larger than ``max_correction_mm`` are refused outright.
2. The proposal is shown to the user. Applying it requires a
   :class:`Confirmation` that carries the proposal's digest, so a confirmation
   given for one proposal can never apply a different one.
3. :class:`OffsetWriter` is abstract. Where the Snapmaker U1 stores tool offsets
   and how they are applied is unknown (TO VERIFY ON HARDWARE), so the only
   implementation shipped is :class:`DryRunWriter`, which writes nothing.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import numpy as np

from aligncam.geometry import rotation_matrix
from aligncam.measure.estimate import Measurement


class ConfirmationError(RuntimeError):
    pass


@dataclass(frozen=True)
class OffsetCorrection:
    tool: int
    measured_dx_mm: float
    """Measured error in printer (bed) axes, already rotated from pattern axes."""
    measured_dy_mm: float
    sigma_x_mm: float
    sigma_y_mm: float
    delta_x_mm: float
    """Proposed change of the X offset (sign convention TO VERIFY ON HARDWARE)."""
    delta_y_mm: float
    significant: bool
    """False when the error is within 2 sigma of zero: no change is needed."""


@dataclass(frozen=True)
class OffsetProposal:
    corrections: tuple[OffsetCorrection, ...]
    skipped: dict[int, list[str]] = field(default_factory=dict)
    """Tools with no proposal, and why."""
    sign: int = -1
    created_at: str = ""

    def digest(self) -> str:
        payload = json.dumps(
            {"c": [asdict(c) for c in self.corrections], "sign": self.sign, "t": self.created_at},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "digest": self.digest(),
            "created_at": self.created_at,
            "sign": self.sign,
            "corrections": [asdict(c) for c in self.corrections],
            "skipped": {f"T{t}": r for t, r in self.skipped.items()},
        }


@dataclass(frozen=True)
class Confirmation:
    proposal_digest: str
    confirmed_by: str
    tools: tuple[int, ...] | None = None
    """Subset of tools to apply; None = all significant corrections."""


@dataclass
class ApplyResult:
    applied: bool
    dry_run: bool
    lines: list[str]


def build_proposal(
    m: Measurement,
    sign: int = -1,
    max_correction_mm: float = 0.3,
    pattern_rotation_deg: int = 0,
) -> OffsetProposal:
    """Proposal in printer axes.

    The measurement is in pattern axes. ``pattern_rotation_deg`` (the layout's
    ``rotation_deg``) turns it into bed X/Y; with a multiple of 90 degrees the two
    sigmas are simply swapped or kept.
    """
    rot = rotation_matrix(pattern_rotation_deg)
    corrections = []
    skipped: dict[int, list[str]] = {}
    for t in sorted(m.tools):
        to = m.tools[t]
        if to.status != "ok" or to.dx_mm is None or to.dy_mm is None:
            skipped[t] = [str(r) for r in to.reasons] or ["not measured"]
            continue
        if max(abs(to.dx_mm), abs(to.dy_mm)) > max_correction_mm:
            skipped[t] = [f"correction larger than {max_correction_mm} mm: check manually"]
            continue
        dx, dy = (rot @ np.array([to.dx_mm, to.dy_mm])).tolist()
        sx, sy = np.sqrt(rot**2 @ np.array([to.sigma_x_mm or 0.0, to.sigma_y_mm or 0.0]) ** 2)
        sx, sy = float(sx), float(sy)
        significant = abs(dx) > 2 * sx or abs(dy) > 2 * sy
        corrections.append(
            OffsetCorrection(
                tool=t,
                measured_dx_mm=round(dx, 4),
                measured_dy_mm=round(dy, 4),
                sigma_x_mm=round(sx, 4),
                sigma_y_mm=round(sy, 4),
                delta_x_mm=round(sign * dx, 4),
                delta_y_mm=round(sign * dy, 4),
                significant=significant,
            )
        )
    return OffsetProposal(
        tuple(corrections), skipped, sign, datetime.now(UTC).isoformat(timespec="seconds")
    )


class OffsetWriter(ABC):
    """Applies a confirmed proposal to the printer. Never call without user consent."""

    def apply(self, proposal: OffsetProposal, confirmation: Confirmation) -> ApplyResult:
        if confirmation.proposal_digest != proposal.digest():
            raise ConfirmationError("confirmation does not match this proposal")
        chosen = [
            c
            for c in proposal.corrections
            if c.significant and (confirmation.tools is None or c.tool in confirmation.tools)
        ]
        return self._apply(chosen, confirmation)

    @abstractmethod
    def describe(self, corrections: list[OffsetCorrection]) -> list[str]:
        """Human-readable description of what applying would do."""

    @abstractmethod
    def _apply(
        self, corrections: list[OffsetCorrection], confirmation: Confirmation
    ) -> ApplyResult: ...


class DryRunWriter(OffsetWriter):
    """Logs what would be changed. Touches nothing."""

    def __init__(self) -> None:
        self.log: list[str] = []

    def describe(self, corrections: list[OffsetCorrection]) -> list[str]:
        lines = []
        for c in corrections:
            lines.append(
                f"T{c.tool}: change X offset by {c.delta_x_mm:+.3f} mm, "
                f"Y offset by {c.delta_y_mm:+.3f} mm"
            )
        if not lines:
            lines.append("No significant correction: nothing to change.")
        lines.append(
            "DRY RUN: nothing was sent to the printer. Where the U1 stores tool offsets and "
            "their sign convention are TO VERIFY ON HARDWARE."
        )
        return lines

    def _apply(
        self, corrections: list[OffsetCorrection], confirmation: Confirmation
    ) -> ApplyResult:
        lines = self.describe(corrections)
        self.log += [f"[{confirmation.confirmed_by}] {ln}" for ln in lines]
        return ApplyResult(applied=False, dry_run=True, lines=lines)
