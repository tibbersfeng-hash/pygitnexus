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

# Read GitHub token for private repo access
# Priority: GITHUB_TOKEN env > GitHub CLI token > anonymous
def _get_github_token() -> str | None:
    """Get GitHub auth token from environment or gh CLI."""
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    # Try GitHub CLI token
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

    # Map OS
    if system == "darwin":
        os_name = "macos"
    elif system == "linux":
        os_name = "linux"
    elif system == "windows":
        os_name = "windows"
    else:
        click.echo(f"  Unsupported OS: {system}")
        sys.exit(1)

    # Map architecture
    arch_map = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }
    arch = arch_map.get(machine)
    if arch is None:
        click.echo(f"  Unsupported architecture: {machine}")
        sys.exit(1)

    return os_name, arch


def _get_install_path() -> Path | None:
    """Find a writable directory in PATH, or fallback to user-local bin."""
    # Check PATH directories first
    for p in os.environ.get("PATH", "").split(os.pathsep):
        path = Path(p)
        if path.is_dir() and os.access(str(path), os.W_OK):
            return path

    # Fallback to user-local bin
    if sys.platform == "win32":
        local_bin = Path.home() / "AppData" / "Local" / "pygitnexus"
    elif sys.platform == "darwin":
        local_bin = Path.home() / ".local" / "bin"
    else:
        local_bin = Path.home() / ".local" / "bin"

    try:
        local_bin.mkdir(parents=True, exist_ok=True)
        return local_bin
    except OSError:
        return None


def _get_latest_release() -> dict | None:
    """Fetch the latest release from GitHub API."""
    headers = _github_headers()
    try:
        resp = httpx.get(f"{GITHUB_API}/latest", headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        # Fallback: list releases
        resp = httpx.get(GITHUB_API, headers=headers, params={"per_page": 1}, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data[0] if data else None
        # Print helpful error for private repos
        if resp.status_code == 404:
            click.echo("  Error: Release not found. If the repo is private, set GITHUB_TOKEN:")
            click.echo("    export GITHUB_TOKEN=ghp_xxxx")
            click.echo("    # or login with GitHub CLI: gh auth login")
        return None
    except Exception as e:
        click.echo(f"  Warning: Could not fetch releases from GitHub: {e}")
    return None


def _find_asset(release: dict, os_name: str, arch: str) -> tuple[str, str] | None:
    """Find matching binary asset in a release.

    Returns (asset_name, download_url) or None.
    """
    ext = ".exe" if os_name == "windows" else ""
    target_name = f"pygitnexus-{release['tag_name']}-{os_name}-{arch}{ext}"

    for asset in release.get("assets", []):
        name = asset.get("name", "")
        # Try exact match first
        if name == target_name:
            return name, asset["browser_download_url"]
        # Try partial match (OS + arch)
        if f"pygitnexus-" in name and f"-{os_name}-" in name and f"-{arch}" in name:
            return name, asset["browser_download_url"]

    # Fallback: any binary matching os-arch pattern
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.endswith(ext) and f"-{os_name}-{arch}" in name:
            return name, asset["browser_download_url"]

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
                click.echo()  # newline after progress
        return True
    except Exception as e:
        click.echo(f"\n  Download failed: {e}")
        return False


@click.command("install")
@click.option("--version", "-v", default=None, help="Install specific version (e.g. v1.0.0)")
@click.option("--path", "-p", "install_path", default=None, type=click.Path(),
              help="Install to specific directory (default: first writable PATH dir)")
@click.option("--force", "-f", is_flag=True, default=False, help="Overwrite existing binary")
def install_cmd(version: str | None, install_path: str | None, force: bool) -> None:
    """Download and install pygitnexus binary to system PATH.

    Automatically detects the current platform and downloads the latest
    release from GitHub.

    Examples:
        pygitnexus install              # Latest version
        pygitnexus install -v v1.0.0    # Specific version
        pygitnexus install -p /usr/local/bin  # Custom path
    """
    click.echo("")
    click.echo("  PyGitNexus Installer")
    click.echo("  ====================")
    click.echo("")

    # Detect platform
    os_name, arch = _detect_platform()
    click.echo(f"  Platform: {os_name} {arch}")
    click.echo(f"  Python:   {platform.python_version()}")
    click.echo("")

    # Fetch release
    headers = _github_headers()
    if version:
        if not version.startswith("v"):
            version = f"v{version}"
        click.echo(f"  Looking for release: {version}")
        try:
            resp = httpx.get(f"{GITHUB_API}/tags/{version}", headers=headers, timeout=30)
            if resp.status_code == 404:
                resp = httpx.get(f"{GITHUB_API}", headers=headers, params={"per_page": 20}, timeout=30)
                release = None
                for r in resp.json():
                    if r.get("tag_name") == version:
                        release = r
                        break
            else:
                release = resp.json() if resp.status_code == 200 else None
        except Exception as e:
            click.echo(f"  Error: Could not fetch release {version}: {e}")
            return
    else:
        click.echo("  Fetching latest release from GitHub...")
        release = _get_latest_release()

    if release is None:
        click.echo(f"  Error: Could not find release{' ' + version if version else ''}")
        return

    click.echo(f"  Release:  {release['tag_name']} ({release.get('name', release['tag_name'])})")
    click.echo("")

    # Find asset
    asset_info = _find_asset(release, os_name, arch)
    if asset_info is None:
        click.echo(f"  Error: No binary available for {os_name}-{arch}")
        click.echo(f"  Available assets:")
        for asset in release.get("assets", []):
            click.echo(f"    - {asset['name']}")
        return

    asset_name, download_url = asset_info
    click.echo(f"  Binary:   {asset_name}")

    # Determine install path
    if install_path:
        dest_dir = Path(install_path)
    else:
        dest_dir = _get_install_path()

    if dest_dir is None:
        click.echo("  Error: Could not find a writable install directory.")
        click.echo("  Use --path to specify a directory manually.")
        return

    click.echo(f"  Install:  {dest_dir}")
    click.echo("")

    # Determine binary name
    binary_name = "pygitnexus.exe" if os_name == "windows" else "pygitnexus"
    dest_file = dest_dir / binary_name

    # Check existing
    if dest_file.exists() and not force:
        click.echo(f"  Binary already exists at: {dest_file}")
        click.echo("  Use --force to overwrite.")
        return

    # Download to temp file first
    temp_file = dest_dir / f".pygitnexus-download-{os_name}-{arch}"
    try:
        ok = _download_file(download_url, temp_file)
        if not ok:
            temp_file.unlink(missing_ok=True)
            return

        # Move into place
        temp_file.rename(dest_file)

        # Set executable on Unix
        if sys.platform != "win32":
            current = dest_file.stat().st_mode
            dest_file.chmod(current | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        click.echo(f"  Installed: {dest_file}")
        click.echo("")

        # Verify
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
                click.echo(f"    {dest_dir}")
        except Exception:
            click.echo(f"  Installed but could not verify. Add to PATH:")
            click.echo(f"    {dest_dir}")

    except Exception as e:
        click.echo(f"  Error: {e}")
        temp_file.unlink(missing_ok=True)
    finally:
        temp_file.unlink(missing_ok=True)

    click.echo("")
