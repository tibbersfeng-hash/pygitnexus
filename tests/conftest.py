"""Shared test fixtures for setup-related tests."""

from pathlib import Path

import pytest
from click.testing import CliRunner


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def fake_bin(tmp_path: Path):
    """Create a fake pygitnexus binary."""
    bin_path = tmp_path / "pygitnexus"
    bin_path.write_text("#!/bin/sh\necho 0.1.0\n", encoding="utf-8")
    bin_path.chmod(0o755)
    return str(bin_path)
