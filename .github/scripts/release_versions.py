#!/usr/bin/env python3
"""Every distribution a release publishes, held to the tag.

The tag names the release and each distribution's metadata names its version; a
distribution whose metadata drifted is a project published under a number nobody
asked for. All of them are checked, not only the base: since keepup-124 the
release uploads the capabilities too, and each is a project of its own on the
index.

    python .github/scripts/release_versions.py 0.4.0   # a tag
    python .github/scripts/release_versions.py         # no tag: the base decides
"""

from __future__ import annotations

import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def metadata_files(root: pathlib.Path = ROOT) -> list[pathlib.Path]:
    """The base's metadata and one per capability, the base first."""
    return [root / "pyproject.toml", *sorted((root / "packages").glob("*/pyproject.toml"))]


def declared_version(metadata: pathlib.Path) -> str:
    """What one distribution says its version is."""
    data = tomllib.loads(metadata.read_text(encoding="utf-8"))
    return data["project"]["version"]


def main(argv: list[str] | None = None, root: pathlib.Path | None = None) -> int:
    """Check every distribution against the tag, or against the base without one."""
    root = ROOT if root is None else pathlib.Path(root)
    argv = list(sys.argv[1:] if argv is None else argv)
    files = metadata_files(root)
    wanted = argv[0] if argv else declared_version(files[0])
    if not argv:
        print("no tag: every distribution is held to the base")

    wrong = []
    for metadata in files:
        found = declared_version(metadata)
        print(f"{metadata.relative_to(root)}: {found}")
        if found != wanted:
            wrong.append(f"{metadata.relative_to(root)} says {found}")

    if wrong:
        print(f"::error::the release is {wanted}, but " + "; ".join(wrong))
        return 1
    print(f"publishing {wanted}: {len(files)} distributions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
