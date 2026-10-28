#!/usr/bin/env python3
"""Deduplicate and filter bundled pip wheels for portable package builds."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


def _version_key(version: str) -> tuple:
    parts: list[object] = []
    for chunk in re.split(r"[.+]", version):
        if chunk.isdigit():
            parts.append(int(chunk))
        elif chunk:
            parts.append(chunk)
    return tuple(parts)


def _parse_wheel(path: Path) -> tuple[str, str, str, str, str]:
    stem = path.name[:-4]
    parts = stem.split("-")
    if len(parts) < 4:
        raise ValueError(f"Unrecognized wheel name: {path.name}")
    platform_tag = parts[-1]
    abi_tag = parts[-2]
    python_tag = parts[-3]
    version = parts[-4]
    name = "-".join(parts[:-4])
    return (
        name.replace("-", "_").lower(),
        version,
        python_tag,
        abi_tag,
        platform_tag,
    )


def _normalize_package_name(name: str) -> str:
    return str(name or "").strip().lower().replace("-", "_")


def _is_excluded_package(filename: str, excluded_packages: set[str]) -> bool:
    if not excluded_packages:
        return False
    try:
        package_name, _, _, _, _ = _parse_wheel(Path(filename))
    except ValueError:
        return False
    return package_name in excluded_packages


def _is_pure_wheel(filename: str) -> bool:
    lower = filename.lower()
    return "-py3-none-any.whl" in lower or "-py2.py3-none-any.whl" in lower


def _matches_platform(filename: str, platform: str) -> bool:
    if _is_pure_wheel(filename):
        return True
    lower = filename.lower()
    if platform == "mac":
        return "macosx" in lower or "universal2" in lower
    if platform == "windows":
        return "win_amd64" in lower or "win32" in lower
    return True


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    best: dict[tuple[str, str, str, str], tuple[tuple, Path]] = {}
    for path in paths:
        try:
            name, version, python_tag, abi_tag, platform_tag = _parse_wheel(path)
        except ValueError:
            continue
        key = (name, python_tag, abi_tag, platform_tag)
        version_key = _version_key(version)
        current = best.get(key)
        if current is None or version_key > current[0]:
            best[key] = (version_key, path)
    return [entry[1] for entry in best.values()]


def dedupe_directory(wheel_dir: Path) -> tuple[int, int]:
    wheels = sorted(wheel_dir.glob("*.whl"))
    keep = set(_dedupe_paths(wheels))
    removed = 0
    for wheel in wheels:
        if wheel not in keep:
            wheel.unlink()
            removed += 1
    return len(keep), removed


def copy_platform_wheels(
    source_dir: Path,
    dest_dir: Path,
    platform: str,
    *,
    exclude_packages: set[str] | None = None,
) -> tuple[int, int]:
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    excluded = {_normalize_package_name(name) for name in (exclude_packages or set()) if name}
    candidates = [
        path
        for path in source_dir.glob("*.whl")
        if _matches_platform(path.name, platform)
        and not _is_excluded_package(path.name, excluded)
    ]
    kept = _dedupe_paths(candidates)
    for wheel in kept:
        shutil.copy2(wheel, dest_dir / wheel.name)
    return len(kept), len(candidates) - len(kept)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dedupe",
        type=Path,
        help="Remove older duplicate wheels from this directory in place.",
    )
    parser.add_argument(
        "--platform",
        choices=("mac", "windows"),
        help="Copy only wheels needed for this platform.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="Source wheel directory for --platform copy.",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        help="Destination wheel directory for --platform copy.",
    )
    parser.add_argument(
        "--exclude-package",
        action="append",
        default=[],
        help="Skip wheels for this package name (repeatable).",
    )
    args = parser.parse_args()

    if args.dedupe:
        kept, removed = dedupe_directory(args.dedupe.expanduser().resolve())
        print(f"Deduplicated {args.dedupe}: kept {kept} wheel(s), removed {removed}.")
        return 0

    if args.platform and args.source and args.dest:
        kept, skipped = copy_platform_wheels(
            args.source.expanduser().resolve(),
            args.dest.expanduser().resolve(),
            args.platform,
            exclude_packages=set(args.exclude_package or []),
        )
        print(
            f"Prepared {args.platform} wheels: copied {kept}, "
            f"skipped {skipped} duplicate or filtered file(s)."
        )
        return 0

    parser.error("Use --dedupe DIR or --platform ... --source ... --dest ...")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
