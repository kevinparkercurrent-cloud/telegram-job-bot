from pathlib import Path

import pytest


@pytest.fixture
def profile_path() -> Path:
    return Path(__file__).parents[1] / "config" / "candidate-profile.full.json"
