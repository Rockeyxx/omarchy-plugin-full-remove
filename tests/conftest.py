import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load():
    spec = importlib.util.spec_from_file_location("fullremove", ROOT / "backend" / "fullremove.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def fr():
    return _load()


@pytest.fixture
def fixture_text():
    return lambda name: (FIXTURES / name).read_text()
