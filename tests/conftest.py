"""Shared fixtures. The generated corpus is built once per session and reused."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

from cardif.config import load_config          # noqa: E402
from cardif.mapping import Mapper              # noqa: E402


@pytest.fixture(scope="session")
def corpus(tmp_path_factory) -> tuple[Path, list[dict]]:
    """Generate the synthetic bank workbooks once, returning the root and answer key."""
    from gen_fixtures import generate

    out = tmp_path_factory.mktemp("corpus")
    truth = generate(out, ["BNA", "BIAT", "STB", "BH", "ATB", "UIB"], 2025, seed=42)
    return out, truth


@pytest.fixture
def config():
    """A fresh config for each test, so learned aliases never leak between tests."""
    return load_config(ROOT / "config")


@pytest.fixture
def mapper(config):
    return Mapper(config)
