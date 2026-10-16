"""Benchmark statistics, plots and Markdown tables.

All numbers produced here come from synthetic images. The report says so
explicitly; keep it that way.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aligncam.bench.sweeps import GRID_BLUR, GRID_GSD, SWEEPS
from aligncam.sim.camera import distance_for_gsd
from aligncam.sim.u1 import PLACEMENTS

TARGET_MM = 0.04
"""Error above which an *accepted* measurement counts as unsafe."""
GOAL_MM = 0.02

# Reference palette (validated categorical slots + text/surface tokens).
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
CRITICAL = "#d03b3b"
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


@dataclass
class Stats:
    n: int
    accepted: float
    """Fraction of measurements accepted."""
    mean_abs_mm: float
    p95_abs_mm: float
    unsafe: float
    """Fraction of ALL measurements that were accepted with |error| > TARGET_MM."""
    coverage_2sigma: float
    """Fraction of accepted measurements with |error| <= 2 sigma."""


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["value"] = float(r["value"])
        r["blur"] = float(r["blur"])
        r["gsd"] = float(r["gsd"])
        r["accepted"] = int(r["accepted"])
        r["error_mm"] = float(r["error_mm"]) if r["error_mm"] else math.nan
        r["sigma_mm"] = float(r["sigma_mm"]) if r["sigma_mm"] else math.nan
    return rows


def stats(rows: list[dict]) -> Stats:
    n = len(rows)
    acc = [r for r in rows if r["accepted"]]
    err = np.abs([r["error_mm"] for r in acc])
    sig = np.array([r["sigma_mm"] for r in acc])
    if len(acc) == 0:
        return Stats(n, 0.0, math.nan, math.nan, 0.0, math.nan)
    return Stats(
        n=n,
        accepted=len(acc) / n,
        mean_abs_mm=float(err.mean()),
        p95_abs_mm=float(np.quantile(err, 0.95)),
        unsafe=float((err > TARGET_MM).sum() / n),
        coverage_2sigma=float((err <= 2 * sig).mean()),
    )


def group(rows: list[dict], sweep: str, key: str = "value") -> dict[float, list[dict]]:
    out: dict[float, list[dict]] = defaultdict(list)
    for r in rows:
        if r["sweep"] == sweep:
            out[r[key]].append(r)
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- plots


def _style(ax, title: str, xlabel: str, ylabel: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=TEXT, fontsize=11, loc="left")
    ax.set_xlabel(xlabel, color=TEXT_2, fontsize=9)
    ax.set_ylabel(ylabel, color=TEXT_2, fontsize=9)
    ax.tick_params(colors=TEXT_2, labelsize=8, length=0)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)


def plot_sweep(rows: list[dict], sweep_name: str, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sw = SWEEPS[sweep_name]
    groups = group(rows, sweep_name)
    xs = np.array(list(groups))
    st = [stats(g) for g in groups.values()]
    p95 = np.array([s.p95_abs_mm for s in st]) * 1000
    mean = np.array([s.mean_abs_mm for s in st]) * 1000
    rej = np.array([1 - s.accepted for s in st]) * 100
    unsafe = np.array([s.unsafe for s in st]) * 100

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.6), facecolor=SURFACE)
    a1.axhspan(GOAL_MM * 1000, TARGET_MM * 1000, color="#eeeeea", zorder=0)
    a1.text(
        xs[-1],
        TARGET_MM * 1000,
        "target band 20-40 um ",
        color=TEXT_2,
        fontsize=8,
        va="bottom",
        ha="right",
    )
    a1.plot(xs, p95, color=BLUE, lw=2, marker="o", ms=5, label="95th percentile")
    a1.plot(xs, mean, color=ORANGE, lw=2, marker="o", ms=5, label="mean")
    _style(a1, "|error| of accepted measurements", sw.label, "micrometres")
    top = max(TARGET_MM * 1000, float(np.nanmax(p95)) if np.isfinite(p95).any() else 0) * 1.35
    a1.set_ylim(0, top)
    a1.legend(frameon=False, fontsize=8, labelcolor=TEXT_2, loc="upper left", ncol=2)

    a2.plot(xs, rej, color=BLUE, lw=2, marker="o", ms=5, label="rejected")
    a2.plot(
        xs,
        unsafe,
        color=CRITICAL,
        lw=2,
        marker="s",
        ms=5,
        label=f"accepted but |error| > {TARGET_MM * 1000:.0f} um (unsafe)",
    )
    _style(a2, "Outcome", sw.label, "% of measurements")
    a2.set_ylim(-2, 125)
    a2.set_yticks([0, 20, 40, 60, 80, 100])
    a2.legend(frameon=False, fontsize=8, labelcolor=TEXT_2, loc="upper left")
    fig.suptitle(
        f"Synthetic benchmark: {sw.label.lower()}", color=TEXT, fontsize=12, x=0.01, ha="left"
    )
    fig.tight_layout()
    fig.savefig(out, dpi=130, facecolor=SURFACE)
    plt.close(fig)


def grid_table(rows: list[dict]) -> dict[tuple[float, float], Stats]:
    cells: dict[tuple[float, float], list[dict]] = defaultdict(list)
    for r in rows:
        if r["sweep"] == "grid":
            cells[(round(r["gsd"], 4), round(r["blur"], 4))].append(r)
    return {k: stats(v) for k, v in cells.items()}


def plot_grid(rows: list[dict], out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap

    tab = grid_table(rows)
    data = np.full((len(GRID_BLUR), len(GRID_GSD)), np.nan)
    acc = np.zeros_like(data)
    for i, b in enumerate(GRID_BLUR):
        for j, g in enumerate(GRID_GSD):
            s = tab.get((round(g, 4), round(b, 4)))
            if s:
                data[i, j] = s.p95_abs_mm * 1000
                acc[i, j] = s.accepted
    bounds = [0, 10, 15, 20, 30, 40, 60, 1000]
    cmap = ListedColormap(SEQ)
    cmap.set_bad(GRID)
    # Cells where most measurements are rejected are drawn neutral: their error
    # statistics describe a handful of survivors and would read as "good".
    shown = np.ma.masked_where(~(acc >= 0.5) | np.isnan(data), data)
    fig, ax = plt.subplots(figsize=(8, 3.8), facecolor=SURFACE)
    ax.imshow(shown, cmap=cmap, norm=BoundaryNorm(bounds, cmap.N), aspect="auto", origin="lower")
    for i in range(len(GRID_BLUR)):
        for j in range(len(GRID_GSD)):
            v = data[i, j]
            if acc[i, j] < 0.5:
                txt, color = f"mostly rejected\n{acc[i, j] * 100:.0f}% ok", TEXT_2
            else:
                txt = f"{v:.0f} um\n{acc[i, j] * 100:.0f}% ok"
                color = "#ffffff" if v >= 20 else TEXT
            ax.text(j, i, txt, ha="center", va="center", fontsize=7.5, color=color)
    ax.set_xticks(range(len(GRID_GSD)), [f"{g:g}" for g in GRID_GSD])
    ax.set_yticks(range(len(GRID_BLUR)), [f"{b:g}" for b in GRID_BLUR])
    _style(
        ax,
        "P95 |error| of accepted measurements (grey: under 50 % accepted)",
        "Ground sampling distance (mm/px)",
        "Blur sigma (px)",
    )
    ax.grid(False)
    fig.tight_layout()
    fig.savefig(out, dpi=130, facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------- tables


def _pct(x: float) -> str:
    return f"{x * 100:.1f} %"


def _um(x: float) -> str:
    return "-" if math.isnan(x) else f"{x * 1000:.1f}"


def stats_table(groups: dict, label: str) -> str:
    lines = [
        f"| {label} | n | accepted | mean abs err (um) | P95 abs err (um) | unsafe "
        "| 2-sigma coverage |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for k, g in groups.items():
        s = stats(g)
        cov = "-" if math.isnan(s.coverage_2sigma) else _pct(s.coverage_2sigma)
        lines.append(
            f"| {k:g} | {s.n} | {_pct(s.accepted)} | {_um(s.mean_abs_mm)} | "
            f"{_um(s.p95_abs_mm)} | {_pct(s.unsafe)} | {cov} |"
        )
    return "\n".join(lines)


def max_gsd(rows: list[dict], p95_limit_mm: float, max_blur: float, min_accept: float = 0.9):
    """Largest grid GSD such that every blur <= max_blur meets the limits."""
    tab = grid_table(rows)
    best = None
    for g in GRID_GSD:
        ok = True
        for b in GRID_BLUR:
            if b > max_blur:
                continue
            s = tab.get((round(g, 4), round(b, 4)))
            if s is None or s.accepted < min_accept or not (s.p95_abs_mm <= p95_limit_mm):
                ok = False
        if ok:
            best = g
        else:
            break
    return best


def camera_table(gsd_limit: float) -> str:
    """Maximum camera-to-pattern distance for common sensors and lenses."""
    resolutions = [
        ("1280 x 720", 1280),
        ("1920 x 1080", 1920),
        ("2592 x 1944 (5 MP)", 2592),
        ("3840 x 2160 (4K)", 3840),
    ]
    fovs = [60, 80, 100, 120]
    head = "| Resolution | " + " | ".join(f"HFOV {f} deg" for f in fovs) + " |"
    lines = [head, "|---|" + "---:|" * len(fovs)]
    for name, w in resolutions:
        cells = [f"{distance_for_gsd(w, f, gsd_limit):.0f} mm" for f in fovs]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def colors_table(rows: list[dict]) -> str:
    sub = [r for r in rows if r["sweep"] == "colors"]
    by: dict[str, list[dict]] = defaultdict(list)
    for r in sub:
        by[r["bed"]].append(r)
    lines = [
        "| Plate | n | accepted | P95 abs err (um) | unsafe | most common rejection |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for bed, g in sorted(by.items()):
        s = stats(g)
        reasons: dict[str, int] = defaultdict(int)
        for r in g:
            for x in filter(None, r["reasons"].split("|")):
                reasons[x] += 1
        top = max(reasons, key=reasons.get) if reasons else "-"
        lines.append(
            f"| {bed} | {s.n} | {_pct(s.accepted)} | {_um(s.p95_abs_mm)} | "
            f"{_pct(s.unsafe)} | {top} |"
        )
    return "\n".join(lines)


HEIGHT_BINS_MM = ((50.0, 100.0), (100.0, 150.0), (150.0, 200.0))


def _top_reason(rows: list[dict]) -> str:
    reasons: dict[str, int] = defaultdict(int)
    for r in rows:
        for x in filter(None, r["reasons"].split("|")):
            reasons[x] += 1
    return max(reasons, key=reasons.get) if reasons else "-"


def u1_table(rows: list[dict], sweep: str = "u1") -> str:
    """Hypothetical U1 side camera: one row per pattern placement."""
    groups = group(rows, sweep)
    lines = [
        "| Pattern placement | n | pattern in frame | accepted | accepted, in frame "
        "| P95 abs err (um) | unsafe | median GSD (mm/px) | median view angle "
        "| accepted, camera 50-100 / 100-150 / 150-200 mm high | most common rejection |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for k, g in groups.items():
        s = stats(g)
        inside = [r for r in g if int(r.get("in_frame") or 0)]
        acc_in = "-" if not inside else _pct(stats(inside).accepted)
        by_h = []
        for lo, hi in HEIGHT_BINS_MM:
            sub = [r for r in g if lo <= float(r["cam_height_mm"]) < hi]
            by_h.append("-" if not sub else f"{stats(sub).accepted * 100:.0f} %")
        lines.append(
            f"| {PLACEMENTS[int(k)].name} | {s.n} | {_pct(len(inside) / s.n)} | "
            f"{_pct(s.accepted)} | {acc_in} | {_um(s.p95_abs_mm)} | {_pct(s.unsafe)} | "
            f"{np.median([r['gsd'] for r in g]):.2f} | "
            f"{np.median([float(r['view_deg']) for r in g]):.0f} deg | "
            f"{' / '.join(by_h)} | {_top_reason(g)} |"
        )
    return "\n".join(lines)


def build_report(results: Path, docs_dir: Path, image_dir: Path) -> str:
    rows = load(results)
    image_dir.mkdir(parents=True, exist_ok=True)
    for name in SWEEPS:
        plot_sweep(rows, name, image_dir / f"bench_{name}.png")
    plot_grid(rows, image_dir / "bench_grid.png")

    n_images = len({r["seed"] for r in rows})
    overall = stats(rows)
    g40 = max_gsd(rows, TARGET_MM, max_blur=1.5)
    g20 = max_gsd(rows, GOAL_MM, max_blur=1.5)
    rel = image_dir.relative_to(docs_dir).as_posix()
    md = [
        "# Benchmark (synthetic data only)",
        "",
        "> Every number on this page comes from **synthetic images** rendered by",
        "> `aligncam.sim` with known ground truth. None of it has been measured on a",
        "> real Snapmaker U1. Real prints and real cameras will be worse in ways the",
        "> simulator does not model. See the README section *Why real hardware is needed*.",
        "",
        f"- Images: **{n_images}**, tool-axis measurements: **{overall.n}**",
        "- Each measurement: one tool (T1-T3), one axis, random true error in +/-0.3 mm.",
        "- Unless swept, every case draws random nuisance parameters: camera tilt 0-35 deg,",
        "  uncorrected distortion k1 in [-0.15, 0], blur 0.5-1.2 px, sensor noise, JPEG",
        "  quality 70-95, uneven light and vignetting, plate texture, per-line placement",
        "  jitter and wobble (5-20 um), bead-width jitter, start blobs, over/under",
        "  extrusion per tool (+/-15 %), directional bead shading (0-0.3).",
        f"- **Unsafe** = accepted, but |error| > {TARGET_MM * 1000:.0f} um. This is the",
        "  failure mode that matters: a rejected measurement costs a reprint, an unsafe",
        "  one would mislead the user.",
        "",
        "## Headline: which camera is good enough?",
        "",
        "The grid below crosses ground sampling distance (mm per pixel on the bed) with",
        "blur. The model is a GSD limit: the largest GSD at which, for every blur up to",
        "1.5 px, at least 90 % of measurements are accepted and the 95th-percentile",
        "error of accepted ones stays within the limit.",
        "",
        f"- P95 <= 40 um: GSD up to **{g40 if g40 else 'none in grid'} mm/px**",
        f"- P95 <= 20 um: GSD up to **{g20 if g20 else 'none in grid'} mm/px**",
        "",
        f"![GSD x blur grid]({rel}/bench_grid.png)",
        "",
    ]
    if g40:
        md += [
            f"Maximum camera-to-pattern distance for GSD <= {g40} mm/px (pinhole model,",
            "pattern near the optical axis, looking straight down. Tilted views and the",
            "image corners have a coarser GSD, so treat these as upper bounds):",
            "",
            camera_table(g40),
            "",
        ]
    for name, sw in SWEEPS.items():
        md += [
            f"## {sw.label}",
            "",
            f"![{name}]({rel}/bench_{name}.png)",
            "",
            stats_table(group(rows, name), sw.label.split(" (")[0]),
            "",
        ]
    if any(r["sweep"] == "u1" for r in rows):
        md += [
            "## Hypothetical U1 side camera",
            "",
            "Public sources place the U1 camera (1920 x 1080) on the right inner side",
            "panel (see [u1-camera.md](u1-camera.md)). Its height, field of view and",
            "aim are unknown, so every case draws them at random: height above the bed",
            "50-200 mm, 20-50 mm beyond the right edge of a 270 mm bed, 60-210 mm along",
            "the panel, HFOV 80-120 deg, aimed at the bed centre, uncorrected k1 in",
            "[-0.15, 0] around the true optical axis. Parts of the pattern outside the",
            "1920 x 1080 frame are not rendered. All other nuisances are drawn as above.",
            "GSD and view angle are taken at the pattern centre. *Pattern in frame* =",
            "the whole pattern falls inside the 1920 x 1080 sensor.",
            "",
            "Uncorrected lens distortion (no camera calibration):",
            "",
            u1_table(rows, "u1"),
            "",
            "Calibrated lens (k1 = 0, i.e. a perfect undistortion with",
            "`camera_matrix`/`dist_coeffs`):",
            "",
            u1_table(rows, "u1_cal"),
            "",
        ]
    md += [
        "## Random filament colours on two plate types",
        "",
        "Four distinct filaments out of white, black, grey, red, blue, yellow, green,",
        "orange on a dark or a gold textured plate (the real U1 plate is TO VERIFY ON",
        "HARDWARE).",
        "",
        colors_table(rows),
        "",
    ]
    text = "\n".join(md)
    (docs_dir / "benchmark.md").write_text(text, encoding="utf-8")
    return text
