from pathlib import Path

import pytest

ASSETS = Path(__file__).parent / "assets"


@pytest.fixture
def minimod_pdf() -> Path:
    return ASSETS / "minimod" / "minimod.pdf"


@pytest.fixture
def encrypted_pdf() -> Path:
    return ASSETS / "minimod" / "encrypted.pdf"
