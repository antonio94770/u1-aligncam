from pathlib import Path

import pytest

from aligncam.config import AppConfig, PatternConfig, load_config

CONFIG = Path(__file__).resolve().parents[1] / "config"


def test_yaml_templates_load_and_match_defaults():
    cfg = load_config(*sorted(CONFIG.glob("*.yaml")))
    assert cfg == AppConfig()


def test_hardware_unknowns_are_flagged():
    for name in ("printer.u1.template.yaml", "camera.u1.template.yaml"):
        assert "TO VERIFY ON HARDWARE" in (CONFIG / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tools": [0, 1]},
        {"vernier_ratio": 1.2},
        {"pitch_mm": 0.6, "line_width_mm": 0.45},
    ],
)
def test_invalid_pattern_rejected(kwargs):
    with pytest.raises(ValueError):
        PatternConfig(**kwargs)
