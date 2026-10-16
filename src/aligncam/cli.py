"""Command-line interface: ``aligncam --help``."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import typer

from aligncam.config import AppConfig, load_config
from aligncam.geometry import build_layout

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Camera-based tool-offset measurement for the Snapmaker U1.",
)

ConfigOpt = typer.Option(None, "--config", "-c", help="YAML config file(s); later files win.")


def _config(paths: list[Path] | None) -> AppConfig:
    return load_config(*paths) if paths else AppConfig()


@app.command()
def pattern(
    out: Path = typer.Option(Path("out/pattern"), help="Output stem (.gcode and .svg)."),
    config: list[Path] | None = ConfigOpt,
) -> None:
    """Generate the calibration pattern G-code and an SVG preview."""
    from aligncam.pattern import generate_gcode, layout_svg

    cfg = _config(config)
    layout = build_layout(cfg.pattern)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".gcode").write_text(generate_gcode(layout, cfg.printer), encoding="utf-8")
    out.with_suffix(".svg").write_text(layout_svg(layout, cfg.printer), encoding="utf-8")
    x0, y0, x1, y1 = layout.bbox()
    typer.echo(f"pattern {x1 - x0:.1f} x {y1 - y0:.1f} mm -> {out}.gcode, {out}.svg")


@app.command()
def simulate(
    out: Path = typer.Option(Path("out/sim"), help="Output stem (.png and .json ground truth)."),
    seed: int = 0,
    gsd: float = typer.Option(0.15, help="Ground sampling distance, mm per pixel."),
    tilt: float = typer.Option(20.0, help="Camera tilt from vertical, degrees."),
    roll: float = 0.0,
    blur: float = typer.Option(0.6, help="Gaussian blur sigma in pixels."),
    random_offsets_mm: float = typer.Option(0.0, help="If > 0, draw random tool errors."),
    config: list[Path] | None = ConfigOpt,
) -> None:
    """Render a synthetic photo of the printed pattern with known tool errors."""
    import numpy as np

    from aligncam.sim import Scenario, random_offsets
    from aligncam.sim import simulate as run_sim
    from aligncam.sim.degrade import Degradation

    cfg = _config(config)
    layout = build_layout(cfg.pattern)
    sc = Scenario(
        seed=seed,
        gsd_mm_per_px=gsd,
        tilt_deg=tilt,
        roll_deg=roll,
        degradation=Degradation(blur_sigma_px=blur),
    )
    if random_offsets_mm > 0:
        rng = np.random.default_rng(seed + 1_000_003)
        sc.offsets_mm = random_offsets(rng, list(cfg.pattern.tools), random_offsets_mm)
    img, js = run_sim(sc, layout).save(out)
    typer.echo(f"{img} ({js} has the ground truth)")


@app.command()
def measure(
    image: Path,
    out: Path | None = typer.Option(None, help="Output stem for .json and _overlay.png."),
    truth: Path | None = typer.Option(None, help="Ground-truth JSON from `simulate`."),
    config: list[Path] | None = ConfigOpt,
) -> None:
    """Measure the tool offsets in a photo. Never writes anything to the printer."""
    from aligncam.measure import measure_image
    from aligncam.measure.overlay import draw_overlay

    cfg = _config(config)
    layout = build_layout(cfg.pattern)
    img = cv2.imread(str(image), cv2.IMREAD_COLOR)
    if img is None:
        raise typer.BadParameter(f"cannot read {image}")
    m = measure_image(img, layout, cfg.measure, cfg.camera)
    result = m.to_dict()
    if truth is not None:
        gt = json.loads(truth.read_text(encoding="utf-8"))["offsets_mm"]
        for name, t in result["tools"].items():
            if name in gt and t["dx_mm"] is not None:
                t["error_vs_truth_mm"] = [
                    round(t["dx_mm"] - gt[name][0], 4),
                    round(t["dy_mm"] - gt[name][1], 4),
                ]
    typer.echo(json.dumps(result["tools"], indent=2))
    typer.echo(f"status: {m.status}" + (f"  warnings: {m.warnings}" if m.warnings else ""))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.with_suffix(".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        cv2.imwrite(str(out.parent / f"{out.name}_overlay.png"), draw_overlay(img, layout, m))


@app.command()
def bench(
    out: Path = typer.Option(Path("out/bench"), help="Directory for results.csv."),
    cases: int = typer.Option(60, help="Images per sweep point."),
    workers: int | None = typer.Option(None, help="Worker processes (default: CPUs - 1)."),
    seed: int = 0,
) -> None:
    """Run the Monte Carlo benchmark on synthetic images (takes minutes)."""
    from aligncam.bench.runner import run_benchmark

    path = run_benchmark(out, cases, workers, seed)
    typer.echo(f"results: {path}")


@app.command("bench-report")
def bench_report(
    results: Path = typer.Option(Path("out/bench/results.csv")),
    docs: Path = typer.Option(Path("docs")),
) -> None:
    """Build plots and docs/benchmark.md from benchmark results."""
    from aligncam.bench.report import build_report

    build_report(results, docs, docs / "images")
    typer.echo(f"wrote {docs / 'benchmark.md'} and plots in {docs / 'images'}")


if __name__ == "__main__":
    app()
