"""Base scanner interface for frontend code analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.models import Action, APICall, Component, Page


@dataclass
class SourceFile:
    """A source file ready for analysis."""
    path: Path
    relative: str
    content: str


class BaseScanner:
    """Abstract base class for frontend scanners."""

    file_extensions: tuple[str, ...] = ()

    def scan_files(self, root: str | Path) -> list[SourceFile]:
        """Scan directory for source files."""
        root = Path(root) if isinstance(root, str) else root
        root = root.resolve()
        results = []
        skip_dirs = {
            "node_modules", "dist", "build", "out", ".git",
            ".next", ".nuxt", ".vite", "coverage",
        }
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(root).parts
            if any(part in skip_dirs for part in rel_parts):
                continue
            if path.suffix in self.file_extensions:
                rel = str(path.relative_to(root))
                results.append(SourceFile(
                    path=path,
                    relative=rel,
                    content=path.read_text(encoding="utf-8", errors="replace"),
                ))
        return results

    def extract_pages(self, files: list[SourceFile]) -> list[Page]:
        """Extract pages from source files."""
        raise NotImplementedError

    def extract_components(self, files: list[SourceFile]) -> list[Component]:
        """Extract components from source files."""
        raise NotImplementedError

    def extract_actions(self, files: list[SourceFile]) -> list[Action]:
        """Extract actions (interactions/API calls) from source files."""
        raise NotImplementedError

    def get_api_calls(self, files: list[SourceFile]) -> list[APICall]:
        """Extract API call sites from source files."""
        raise NotImplementedError
