import pytest

from aligncam.geometry import build_layout


@pytest.fixture(scope="session")
def layout():
    return build_layout()
