"""Install command: download and install pygitnexus binary to system PATH."""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import click
import httpx

GITHUB_REPO = "tibbersfeng-hash/pygitnexus"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases"


def _get_github_token() -> str | None:
    """Get GitHub auth token from environment or gh CLI."""
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        token = subprocess.check_output(
            ["gh", "auth", "token"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        if token:
            return token
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    return None


def _github_headers() -> dict:
    """Build GitHub API request headers with optional auth."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "pygitnexus-installer",
    }
    token = _get_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _detect_platform() -> tuple[str, str]:
    """Detect current OS and architecture."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        os_name = "macos"
    elif system == "linux":
        os_name = "linux"
    elif system == "windows":
        os_name = "windows"
    else:
        click.echo(f"  Unsupported OS: {system}")
        sys.exit(1)

    arch_map = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }
    arch = arch_map.get(machine)
    if os_name == "windows" and arch == "aarch64":
        arch = "x86_64"
    if arch is None:
        click.echo(f"  Unsupported architecture: {machine}")
        sys.exit(1)

    return os_name, arch


def _get_install_path() -> Path | None:
    """Find a writable directory in PATH, or fallback to user-local bin."""
    for p in os.environ.get("PATH", "").split(os.pathsep):
        path = Path(p)
        if path.is_dir() and os.access(str(path), os.W_OK):
            return path

    local_bin = Path.home() / ".local" / "bin"
    try:
        local_bin.mkdir(parents=True, exist_ok=True)
        return local_bin
    except OSError:
        return None


def _get_release(version: str | None) -> dict | None:
    """Fetch a release from GitHub API."""
    headers = _github_headers()
    if version:
        if not version.startswith("v"):
            version = f"v{version}"
        click.echo(f"  Looking for release: {version}")
        try:
            resp = httpx.get(f"{GITHUB_API}/tags/{version}", headers=headers, timeout=30)
            if resp.status_code == 404:
                resp = httpx.get(GITHUB_API, headers=headers, params={"per_page": 20}, timeout=30)
                for r in resp.json():
                    if r.get("tag_name") == version:
                        return r
                return None
            return resp.json() if resp.status_code == 200 else None
        except Exception as e:
            click.echo(f"  Error: Could not fetch release {version}: {e}")
            return None
    else:
        click.echo("  Fetching latest release from GitHub...")
        try:
            resp = httpx.get(f"{GITHUB_API}/latest", headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            resp = httpx.get(GITHUB_API, headers=headers, params={"per_page": 1}, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return data[0] if data else None
            if resp.status_code == 404:
                click.echo("  Error: Release not found. If the repo is private, set GITHUB_TOKEN:")
                click.echo("    export GITHUB_TOKEN=ghp_xxxx")
                click.echo("    # or install with GitHub CLI: gh auth login")
            return None
        except Exception as e:
            click.echo(f"  Warning: Could not fetch releases from GitHub: {e}")
            return None


def _find_asset(release: dict, os_name: str, arch: str) -> tuple[str, str] | None:
    """Find matching binary asset in a release.

    Returns (asset_name, download_url) or None.
    """
    search_arch = [f"{os_name}-{arch}"]
    if os_name == "macos" and arch == "aarch64":
        search_arch.append("macos-arm64")

    candidates = []
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        for sa in search_arch:
            if f"pygitnexus-" in name and f"-{sa}" in name and "-onedir" not in name:
                candidates.append((name, asset["browser_download_url"]))
                break

    if candidates:
        for name, url in candidates:
            if f"-{os_name}-{arch}" in name:
                return name, url
        return candidates[0]

    return None


def _download_file(url: str, dest: Path) -> bool:
    """Download a file with progress bar."""
    headers = _github_headers()
    try:
        with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=120) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = int(downloaded / total * 100)
                        bar_len = 30
                        filled = int(bar_len * downloaded / total)
                        bar = "=" * filled + ">" + " " * (bar_len - filled - 1)
                        click.echo(f"\r  Downloading: [{bar}] {pct}%", nl=False)
                click.echo()
        return True
    except Exception as e:
        click.echo(f"\n  Download failed: {e}")
        return False


def _verify_and_report(dest_file: Path) -> None:
    """Verify the installed binary and report result."""
    click.echo(f"  Install path: {dest_file}")
    click.echo("  Verifying installation...")
    try:
        result = os.popen(f'"{dest_file}" --version').read().strip()
        if result:
            click.echo(f"  Version:  {result}")
            click.echo("")
            click.echo("  Success! pygitnexus is now available in your PATH.")
        else:
            click.echo(f"  Warning: Binary installed but --version returned empty.")
            click.echo(f"  You may need to add this directory to your PATH:")
            click.echo(f"    {dest_file.parent}")
    except Exception:
        click.echo(f"  Installed but could not verify. Add to PATH:")
        click.echo(f"    {dest_file.parent}")


def _install_binary(src: Path, dest_file: Path) -> None:
    """Copy a binary to the install destination."""
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dest_file))
    click.echo(f"  Copied: {src} -> {dest_file}")

    if sys.platform != "win32":
        current = dest_file.stat().st_mode
        dest_file.chmod(current | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    click.echo("")
    _verify_and_report(dest_file)
    click.echo("")


def _find_self_binary() -> Path | None:
    """Detect if running as a PyInstaller binary and return the binary path."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    argv0 = Path(sys.argv[0]).resolve()
    if argv0.name.startswith("pygitnexus") and argv0.is_file():
        try:
            with open(argv0, "rb") as f:
                header = f.read(4)
                if header[:4] in (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"MZ"):
                    return argv0
        except OSError:
            pass
    return None


def _install_from_github(version: str | None, os_name: str, arch: str,
                         install_path: str | None) -> None:
    """Download and install single binary from GitHub Releases."""
    release = _get_release(version)
    if release is None:
        click.echo(f"  Error: Could not find release{' ' + version if version else ''}")
        return

    click.echo(f"  Release:  {release['tag_name']}")
    click.echo("")

    asset_info = _find_asset(release, os_name, arch)
    if asset_info is None:
        if os_name == "macos" and arch == "aarch64":
            click.echo(f"  Error: No binary available for {os_name}-{arch} or {os_name}-arm64")
        else:
            click.echo(f"  Error: No binary available for {os_name}-{arch}")
        click.echo(f"  Available assets:")
        for asset in release.get("assets", []):
            click.echo(f"    - {asset['name']}")
        return

    asset_name, download_url = asset_info
    dest_dir = Path(install_path) if install_path else _get_install_path()
    if dest_dir is None:
        click.echo("  Error: Could not find a writable install directory.")
        return

    binary_name = "pygitnexus.exe" if os_name == "windows" else "pygitnexus"
    dest_file = dest_dir / binary_name
    click.echo(f"  Binary:   {asset_name}")
    click.echo(f"  Install:  {dest_dir}")
    click.echo("")

    temp_file = dest_dir / f".pygitnexus-download-{os_name}-{arch}"
    try:
        ok = _download_file(download_url, temp_file)
        if not ok:
            return

        temp_file.rename(dest_file)

        if sys.platform != "win32":
            current = dest_file.stat().st_mode
            dest_file.chmod(current | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        click.echo(f"  Installed: {dest_file}")
        click.echo("")
        _verify_and_report(dest_file)

    except Exception as e:
        click.echo(f"  Error: {e}")
    finally:
        temp_file.unlink(missing_ok=True)

    click.echo("")


@click.command("install")
@click.option("--version", "-v", default=None, help="Install specific version (e.g. v1.0.0)")
@click.option("--path", "-p", "install_path", default=None, type=click.Path(),
              help="Install to specific directory (default: first writable PATH dir)")
@click.option("--file", "local_file", default=None, type=click.Path(exists=True),
              help="Install from a local binary file instead of downloading from GitHub")
def install_cmd(version: str | None, install_path: str | None, local_file: str | None) -> None:
    """Download and install pygitnexus binary to system PATH.

    Priority:
      1. --file PATH     : Copy specified local binary
      2. Running binary   : Install self (PyInstaller build)
      3. From source      : Download from GitHub Releases

    Examples:
        pygitnexus install                  # Download latest from GitHub
        pygitnexus install -v v1.0.0        # Specific version from GitHub
        pygitnexus install -p /usr/local/bin  # Custom install directory
        pygitnexus install --file ./dist/pygitnexus  # From local file
    """
    click.echo("")
    click.echo("  PyGitNexus Installer")
    click.echo("  ====================")
    click.echo("")

    os_name, arch = _detect_platform()
    click.echo(f"  Platform: {os_name} {arch}")
    click.echo(f"  Python:   {platform.python_version()}")
    click.echo("")

    # --- Mode 1: --file (explicit local file) ---
    if local_file:
        dest_dir = Path(install_path) if install_path else _get_install_path()
        if dest_dir is None:
            click.echo("  Error: Could not find a writable install directory.")
            click.echo("  Use --path to specify a directory manually.")
            return
        binary_name = "pygitnexus.exe" if os_name == "windows" else "pygitnexus"
        dest_file = dest_dir / binary_name
        click.echo(f"  Source:   {local_file} (local file)")
        click.echo(f"  Install:  {dest_dir}")
        click.echo("")
        _install_binary(Path(local_file).resolve(), dest_file)
        return

    # --- Mode 2: Running as PyInstaller binary? Install self ---
    self_binary = _find_self_binary()
    if self_binary and self_binary.is_file():
        dest_dir = Path(install_path) if install_path else _get_install_path()
        if dest_dir is None:
            click.echo("  Error: Could not find a writable install directory.")
            return
        binary_name = "pygitnexus.exe" if os_name == "windows" else "pygitnexus"
        dest_file = dest_dir / binary_name
        click.echo(f"  Source:   {self_binary} (self)")
        click.echo(f"  Install:  {dest_dir}")
        click.echo("")
        _install_binary(self_binary, dest_file)
        return

    # --- Mode 3: Running from source, download from GitHub ---
    click.echo(f"  Running from source, downloading from GitHub...")
    click.echo("")
    _install_from_github(version, os_name, arch, install_path)
