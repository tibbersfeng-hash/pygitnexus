"""Unit tests for install command cross-platform logic."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pygitnexus.cli.install import (
    _detect_platform,
    _find_asset,
    _find_self_binary,
    _get_install_path,
)


class TestDetectPlatform:
    """Test _detect_platform for all supported OS/arch combos."""

    def _mock(self, system: str, machine: str) -> tuple[str, str]:
        with patch("platform.system", return_value=system), \
             patch("platform.machine", return_value=machine):
            return _detect_platform()

    def test_linux_x86_64(self):
        assert self._mock("Linux", "x86_64") == ("linux", "x86_64")

    def test_linux_aarch64(self):
        assert self._mock("Linux", "aarch64") == ("linux", "aarch64")

    def test_macos_arm64(self):
        assert self._mock("Darwin", "arm64") == ("macos", "aarch64")

    def test_macos_x86_64(self):
        assert self._mock("Darwin", "x86_64") == ("macos", "x86_64")

    def test_windows_x86_64(self):
        assert self._mock("Windows", "AMD64") == ("windows", "x86_64")

    def test_windows_arm64(self):
        # Windows ARM64 falls back to x86_64 (no native ARM release yet)
        assert self._mock("Windows", "ARM64") == ("windows", "x86_64")

    def test_unsupported_os(self):
        with patch("platform.system", return_value="FreeBSD"), \
             patch("platform.machine", return_value="x86_64"), \
             pytest.raises(SystemExit):
            _detect_platform()


class TestFindAsset:
    """Test _find_asset matches assets correctly for each platform."""

    @staticmethod
    def _release(*assets: str) -> dict:
        return {
            "tag_name": "v4",
            "name": "v4",
            "assets": [
                {"name": a, "browser_download_url": f"https://example.com/{a}"}
                for a in assets
            ],
        }

    def test_linux_x86_64(self):
        release = self._release(
            "pygitnexus-v4-linux-aarch64",
            "pygitnexus-v4-linux-x86_64",
            "pygitnexus-v4-macos-arm64",
            "pygitnexus-v4-windows-x86_64.exe",
        )
        name, url = _find_asset(release, "linux", "x86_64")
        assert name == "pygitnexus-v4-linux-x86_64"
        assert "linux-x86_64" in url

    def test_linux_aarch64(self):
        release = self._release(
            "pygitnexus-v4-linux-aarch64",
            "pygitnexus-v4-linux-x86_64",
            "pygitnexus-v4-macos-arm64",
        )
        name, url = _find_asset(release, "linux", "aarch64")
        assert name == "pygitnexus-v4-linux-aarch64"

    def test_macos_aarch64_matches_arm64_asset(self):
        """macOS aarch64 should match 'macos-arm64' asset name."""
        release = self._release(
            "pygitnexus-v4-linux-x86_64",
            "pygitnexus-v4-macos-arm64",
            "pygitnexus-v4-windows-x86_64.exe",
        )
        name, url = _find_asset(release, "macos", "aarch64")
        assert name == "pygitnexus-v4-macos-arm64"

    def test_macos_aarch64_prefers_aarch64_over_arm64(self):
        """If both aarch64 and arm64 assets exist, prefer aarch64."""
        release = self._release(
            "pygitnexus-v4-macos-aarch64",
            "pygitnexus-v4-macos-arm64",
        )
        name, url = _find_asset(release, "macos", "aarch64")
        assert name == "pygitnexus-v4-macos-aarch64"

    def test_windows_x86_64(self):
        release = self._release(
            "pygitnexus-v4-linux-x86_64",
            "pygitnexus-v4-macos-arm64",
            "pygitnexus-v4-windows-x86_64.exe",
        )
        name, url = _find_asset(release, "windows", "x86_64")
        assert name == "pygitnexus-v4-windows-x86_64.exe"
        assert name.endswith(".exe")

    def test_no_matching_asset(self):
        release = self._release(
            "pygitnexus-v4-linux-x86_64",
        )
        result = _find_asset(release, "windows", "x86_64")
        assert result is None

    def test_onefile_excludes_onedir(self):
        """Onefile finder should not match onedir assets."""
        release = self._release(
            "pygitnexus-v17-linux-x86_64",
            "pygitnexus-v17-linux-x86_64-onedir.tar.gz",
        )
        name, url = _find_asset(release, "linux", "x86_64")
        assert "onedir" not in name
        assert name == "pygitnexus-v17-linux-x86_64"


class TestFindSelfBinary:
    """Test _find_self_binary detection logic."""

    def test_frozen_sys(self):
        """When sys.frozen is True (PyInstaller), should return sys.executable."""
        with patch("pygitnexus.cli.install.sys") as mock_sys:
            mock_sys.frozen = True
            mock_sys.executable = "/usr/local/bin/pygitnexus"
            with patch.object(Path, "is_file", return_value=True):
                result = _find_self_binary()
                assert result == Path("/usr/local/bin/pygitnexus")

    def test_not_frozen_no_binary(self):
        """When not frozen and no argv0 binary, return None."""
        with patch("pygitnexus.cli.install.sys") as mock_sys:
            mock_sys.frozen = False
            mock_sys.argv = ["uv", "run", "pygitnexus", "install"]
            mock_sys.executable = "/usr/bin/python3"
            result = _find_self_binary()
            assert result is None


class TestWindowsInstallSimulation:
    """Simulate the Windows install flow using mocks."""

    def test_binary_name_is_pygtnexus_exe(self):
        """On Windows, binary name should be pygitnexus.exe."""
        with patch("platform.system", return_value="Windows"), \
             patch("platform.machine", return_value="AMD64"):
            os_name, arch = _detect_platform()
            binary_name = "pygitnexus.exe" if os_name == "windows" else "pygitnexus"
            assert binary_name == "pygitnexus.exe"
            assert os_name == "windows"
            assert arch == "x86_64"

    def test_asset_has_exe_extension(self):
        """Windows asset should end with .exe."""
        release = {
            "tag_name": "v4",
            "assets": [
                {"name": "pygitnexus-v4-linux-x86_64", "browser_download_url": "https://a"},
                {"name": "pygitnexus-v4-macos-arm64", "browser_download_url": "https://b"},
                {"name": "pygitnexus-v4-windows-x86_64.exe", "browser_download_url": "https://c"},
            ],
        }
        name, url = _find_asset(release, "windows", "x86_64")
        assert name.endswith(".exe")
        assert name == "pygitnexus-v4-windows-x86_64.exe"
