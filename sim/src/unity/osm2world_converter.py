from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


# -----------------------------------------------------------------------------
# Project paths
# -----------------------------------------------------------------------------
# Expected location of this file in your project:
#     sim/src/unity/osm2world_converter.py
#
# PROJECT_ROOT therefore resolves to:
#     sim/
PROJECT_ROOT = Path(__file__).resolve().parents[2]

OSM_CACHE_DIR = PROJECT_ROOT / "cache" / "osm_chunks" / "stuttgart"
GLB_CACHE_DIR = PROJECT_ROOT / "cache" / "glb_chunks" / "stuttgart"
OSM2WORLD_DIR = PROJECT_ROOT / "tools" / "osm2world"


# -----------------------------------------------------------------------------
# Chunk paths
# -----------------------------------------------------------------------------
def osm_chunk_path(cx: int, cy: int) -> Path:
    """Return the existing OSM cache path for a simulation chunk."""
    return OSM_CACHE_DIR / f"chunk_{cx}_{cy}.osm"


def glb_chunk_path(cx: int, cy: int) -> Path:
    """Return the GLB cache path for a simulation chunk."""
    return GLB_CACHE_DIR / f"chunk_{cx}_{cy}.glb"


def glb_exists(cx: int, cy: int) -> bool:
    """True only when a non-empty GLB cache file exists."""
    path = glb_chunk_path(cx, cy)
    return path.is_file() and path.stat().st_size > 0


# -----------------------------------------------------------------------------
# OSM2World launcher discovery
# -----------------------------------------------------------------------------
def _java_available(java_command: str) -> bool:
    candidate = Path(java_command).expanduser()
    return candidate.is_file() or shutil.which(java_command) is not None


def find_osm2world_launcher(
    osm2world_dir: Path = OSM2WORLD_DIR,
    java_command: str = "java",
) -> tuple[str, list[str]]:
    """
    Find a local OSM2World launcher.

    Supported layouts include a Windows .bat launcher, an executable script,
    or a standalone OSM2World.jar. The returned list is the command prefix to
    which the OSM2World arguments can be appended.
    """
    osm2world_dir = Path(osm2world_dir).resolve()

    if not osm2world_dir.exists():
        raise FileNotFoundError(
            f"OSM2World directory does not exist: {osm2world_dir}"
        )

    # Current Windows distributions commonly include a batch launcher.
    if os.name == "nt":
        for name in (
            "osm2world.bat",
            "OSM2World.bat",
            "osm2world-windows.bat",
        ):
            launcher = osm2world_dir / name
            if launcher.is_file():
                return "batch", [
                    "cmd",
                    "/d",
                    "/s",
                    "/c",
                    str(launcher),
                ]

    # Also support shell/native launchers when present.
    for name in ("osm2world", "OSM2World", "osm2world.sh"):
        launcher = osm2world_dir / name
        if launcher.is_file():
            return "executable", [str(launcher)]

    # JAR fallback.
    for name in ("OSM2World.jar", "osm2world.jar"):
        jar = osm2world_dir / name
        if jar.is_file():
            if not _java_available(java_command):
                raise RuntimeError(
                    f"Java executable not found: {java_command!r}. "
                    "Install Java 17+ or provide the full Java path."
                )
            return "jar", [java_command, "-jar", str(jar)]

    raise FileNotFoundError(
        "Could not find an OSM2World launcher in "
        f"{osm2world_dir}. Expected a .bat launcher, executable, or JAR."
    )


# -----------------------------------------------------------------------------
# Conversion
# -----------------------------------------------------------------------------
def build_osm2world_command(
    launcher: list[str],
    source: Path,
    destination: Path,
    *,
    config: Path | None = None,
    legacy_cli: bool = False,
) -> list[str]:
    """Build the OSM2World CLI command for one OSM -> GLB conversion."""
    args = [
        "-i",
        str(source.resolve()),
        "-o",
        str(destination.resolve()),
    ]

    if config is not None:
        args.extend(["-c", str(config.resolve())])

    # OSM2World 0.5 uses the 'convert' subcommand. Older releases accepted
    # the same -i/-o arguments directly. Keeping this switch makes the helper
    # usable with both layouts.
    if legacy_cli:
        return launcher + args

    return launcher + ["convert"] + args


def convert_osm_to_glb(
    source: Path,
    destination: Path,
    *,
    osm2world_dir: Path = OSM2WORLD_DIR,
    config: Path | None = None,
    java_command: str = "java",
    overwrite: bool = False,
    legacy_cli: bool = False,
) -> Path:
    """
    Convert one existing .osm file to .glb using the local OSM2World install.

    The conversion is written to a temporary file first and moved into the GLB
    cache only after OSM2World succeeds. This prevents Unity from observing a
    partially-written GLB while procedural generation is running.
    """
    source = Path(source).resolve()
    destination = Path(destination).resolve()

    if not source.is_file():
        raise FileNotFoundError(f"OSM input does not exist: {source}")

    if destination.is_file() and destination.stat().st_size > 0 and not overwrite:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)

    launcher_kind, launcher = find_osm2world_launcher(
        osm2world_dir=osm2world_dir,
        java_command=java_command,
    )

    temporary_output = destination.with_name(
        f"{destination.stem}.tmp.glb"
    )
    temporary_output.unlink(missing_ok=True)

    command = build_osm2world_command(
        launcher,
        source,
        temporary_output,
        config=config,
        legacy_cli=legacy_cli,
    )

    print(f"[osm2world] launcher: {launcher_kind}")
    print(f"[osm2world] input:    {source}")
    print(f"[osm2world] output:   {destination}")

    try:
        completed = subprocess.run(
            command,
            cwd=Path(osm2world_dir).resolve(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            errors="replace",
        )
    except OSError as exc:
        temporary_output.unlink(missing_ok=True)
        raise RuntimeError(f"Could not launch OSM2World: {exc}") from exc

    output = completed.stdout.strip()

    if completed.returncode != 0:
        temporary_output.unlink(missing_ok=True)
        raise RuntimeError(
            "OSM2World conversion failed"
            + (f":\n{output}" if output else f" (exit code {completed.returncode})")
        )

    if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
        temporary_output.unlink(missing_ok=True)
        raise RuntimeError(
            "OSM2World exited successfully but did not create a non-empty GLB."
        )

    temporary_output.replace(destination)
    print(f"[osm2world] saved:    {destination}")
    return destination


def convert_chunk(
    cx: int,
    cy: int,
    *,
    overwrite: bool = False,
    osm2world_dir: Path = OSM2WORLD_DIR,
    config: Path | None = None,
    java_command: str = "java",
    legacy_cli: bool = False,
) -> Path:
    """Convert one of this project's cached 250 m OSM chunks to GLB."""
    source = osm_chunk_path(cx, cy)
    destination = glb_chunk_path(cx, cy)

    if not source.is_file():
        raise FileNotFoundError(
            f"OSM chunk ({cx}, {cy}) is not cached: {source}\n"
            "Download the OSM chunk first with your existing OSM downloader."
        )

    if glb_exists(cx, cy) and not overwrite:
        print(f"[osm2world] using cached GLB: {destination}")
        return destination

    return convert_osm_to_glb(
        source,
        destination,
        osm2world_dir=osm2world_dir,
        config=config,
        java_command=java_command,
        overwrite=overwrite,
        legacy_cli=legacy_cli,
    )


def convert_cached_chunks(
    *,
    overwrite: bool = False,
    osm2world_dir: Path = OSM2WORLD_DIR,
    config: Path | None = None,
    java_command: str = "java",
    legacy_cli: bool = False,
) -> tuple[int, int, int]:
    """Convert every currently cached Stuttgart OSM chunk."""
    if not OSM_CACHE_DIR.is_dir():
        raise FileNotFoundError(f"OSM cache does not exist: {OSM_CACHE_DIR}")

    osm_files = sorted(OSM_CACHE_DIR.glob("chunk_*_*.osm"))
    converted = 0
    skipped = 0
    failed = 0

    for index, source in enumerate(osm_files, start=1):
        destination = GLB_CACHE_DIR / f"{source.stem}.glb"

        if destination.is_file() and destination.stat().st_size > 0 and not overwrite:
            print(f"[{index}/{len(osm_files)}] cached  {destination.name}")
            skipped += 1
            continue

        print(f"[{index}/{len(osm_files)}] convert {source.name}")
        try:
            convert_osm_to_glb(
                source,
                destination,
                osm2world_dir=osm2world_dir,
                config=config,
                java_command=java_command,
                overwrite=overwrite,
                legacy_cli=legacy_cli,
            )
            converted += 1
        except Exception as exc:
            failed += 1
            print(f"  ERROR: {exc}", file=sys.stderr)

    return converted, skipped, failed


# -----------------------------------------------------------------------------
# Standalone test / utility
# -----------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert this project's cached Stuttgart OSM chunks to GLB."
    )

    parser.add_argument("cx", type=int, nargs="?", help="chunk x coordinate")
    parser.add_argument("cy", type=int, nargs="?", help="chunk y coordinate")
    parser.add_argument(
        "--all",
        action="store_true",
        default=True,
        help="convert every .osm chunk currently present in the Stuttgart cache",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="regenerate GLBs that already exist",
    )
    parser.add_argument(
        "--osm2world-dir",
        type=Path,
        default=OSM2WORLD_DIR,
        help=f"OSM2World installation directory (default: {OSM2WORLD_DIR})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="optional OSM2World .properties config file",
    )
    parser.add_argument(
        "--java",
        default="java",
        help="Java executable used for the JAR fallback",
    )
    parser.add_argument(
        "--legacy-cli",
        action="store_true",
        help="use the older OSM2World CLI without the 'convert' subcommand",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.all:
        converted, skipped, failed = convert_cached_chunks(
            overwrite=args.overwrite,
            osm2world_dir=args.osm2world_dir,
            config=args.config,
            java_command=args.java,
            legacy_cli=args.legacy_cli,
        )
        print()
        print(f"Converted: {converted}")
        print(f"Cached:    {skipped}")
        print(f"Failed:    {failed}")
        return 1 if failed else 0

    if args.cx is None or args.cy is None:
        raise SystemExit(
            "Specify a chunk as: osm2world_converter.py <cx> <cy>\n"
            "or use --all to convert every cached chunk."
        )

    result = convert_chunk(
        args.cx,
        args.cy,
        overwrite=args.overwrite,
        osm2world_dir=args.osm2world_dir,
        config=args.config,
        java_command=args.java,
        legacy_cli=args.legacy_cli,
    )

    print(f"GLB ready: {result}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
