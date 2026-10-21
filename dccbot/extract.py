"""Archive extraction helpers for the optional auto-extract feature.

Supports tar (incl. .tar.gz/.tgz, .tar.bz2, .tar.xz), zip, rar and 7z
archives. Archives found inside extracted output are extracted recursively
up to a configurable depth, similar to SABnzbd's unpacker.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import IO, Any, cast

import py7zr
import rarfile

logger = logging.getLogger(__name__)

TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
ZIP_SUFFIXES = (".zip",)
SEVEN_SUFFIXES = (".7z",)
SEVEN_MULTI_RE = re.compile(r"\.7z\.\d{3}$", re.IGNORECASE)
RAR_NEW_VOLUME_RE = re.compile(r"\.part(\d+)\.rar$", re.IGNORECASE)
RAR_OLD_VOLUME_RE = re.compile(r"\.[rs]\d{2,3}$", re.IGNORECASE)


def archive_kind(filename: str) -> str | None:
    """Classify a filename as 'tar', 'rar', 'zip', '7z' or None.

    Later volumes of multi-part rar sets (``foo.part02.rar``, ``foo.r00``)
    and multi-volume 7z archives (``foo.7z.001``, not supported by py7zr)
    are not independently extractable and return None.
    """
    lower = filename.lower()
    if lower.endswith(TAR_SUFFIXES):
        return "tar"
    if lower.endswith(ZIP_SUFFIXES):
        return "zip"
    if lower.endswith(SEVEN_SUFFIXES):
        return "7z"
    if SEVEN_MULTI_RE.search(lower):
        return None
    if lower.endswith(".rar"):
        volume = RAR_NEW_VOLUME_RE.search(lower)
        if volume and int(volume.group(1)) > 1:
            return None
        return "rar"
    if RAR_OLD_VOLUME_RE.search(lower):
        return None
    return None


def _safe_join(dest_dir: Path, member_name: str) -> Path | None:
    """Resolve a member name inside dest_dir, or None if it escapes."""
    base = dest_dir.resolve()
    target = (base / member_name).resolve()
    if target != base and base not in target.parents:
        return None
    return target


def _unique_path(target: Path) -> Path:
    """Return a non-existing variant of target by appending a numeric suffix."""
    if not target.exists():
        return target
    for i in range(1, 1000):
        candidate = target.with_name(f"{target.stem}.{i}{target.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Cannot find unique name for {target}")


def _write_member(target: Path, src: IO[bytes], out: list[Path]) -> None:
    """Write one archive member stream to a unique path inside dest_dir."""
    target.parent.mkdir(parents=True, exist_ok=True)
    final = _unique_path(target)
    with src, open(final, "wb") as dst:
        shutil.copyfileobj(src, dst)
    out.append(final)


def _extract_tar(archive: Path, dest_dir: Path, out: list[Path], errors: list[str]) -> None:
    """Extract regular files and dirs from a tar archive, member by member."""
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            target = _safe_join(dest_dir, member.name)
            if target is None:
                logger.warning("Skipping unsafe tar member %r in %s", member.name, archive)
                continue
            try:
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isreg():
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    _write_member(target, src, out)
                # Links, devices and fifos are skipped entirely.
            except Exception as e:
                errors.append(f"{member.name}: {e}")


def _extract_zip(archive: Path, dest_dir: Path, out: list[Path], errors: list[str]) -> None:
    """Extract regular files and dirs from a zip archive, member by member."""
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            target = _safe_join(dest_dir, info.filename)
            if target is None:
                logger.warning("Skipping unsafe zip member %r in %s", info.filename, archive)
                continue
            try:
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                elif stat.S_ISLNK(info.external_attr >> 16):
                    logger.warning("Skipping symlink member %r in %s", info.filename, archive)
                else:
                    _write_member(target, zf.open(info), out)
            except Exception as e:
                errors.append(f"{info.filename}: {e}")


def _extract_rar(archive: Path, dest_dir: Path, out: list[Path], errors: list[str]) -> None:
    """Extract regular files and dirs from a rar archive, member by member.

    Requires an external backend tool (unrar, unar, bsdtar or 7z) on PATH;
    rarfile raises if none is available.
    """
    with rarfile.RarFile(archive) as rf:
        for info in rf.infolist():
            target = _safe_join(dest_dir, info.filename)
            if target is None:
                logger.warning("Skipping unsafe rar member %r in %s", info.filename, archive)
                continue
            try:
                if info.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif info.is_symlink():
                    logger.warning("Skipping symlink member %r in %s", info.filename, archive)
                else:
                    _write_member(target, cast("IO[bytes]", rf.open(info)), out)
            except Exception as e:
                errors.append(f"{info.filename}: {e}")


def _extract_7z(archive: Path, dest_dir: Path, out: list[Path], errors: list[str]) -> None:
    """Extract a 7z archive via a staging dir so members get unique names.

    py7zr cannot remap member names during extraction, so files are first
    extracted into a temporary directory inside dest_dir and then moved to
    their final (possibly uniquified) locations.
    """
    with tempfile.TemporaryDirectory(dir=dest_dir) as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        with py7zr.SevenZipFile(archive) as szf:
            szf.extract(path=tmpdir, targets=szf.getnames())
        for src in sorted(tmpdir.rglob("*")):
            rel = src.relative_to(tmpdir)
            if src.is_symlink():
                logger.warning("Skipping symlink member %r in %s", rel.as_posix(), archive)
                continue
            if src.is_dir():
                continue
            target = _safe_join(dest_dir, rel.as_posix())
            if target is None:
                logger.warning("Skipping unsafe 7z member %r in %s", rel.as_posix(), archive)
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                final = _unique_path(target)
                shutil.move(str(src), final)
            except Exception as e:
                errors.append(f"{rel.as_posix()}: {e}")
            else:
                out.append(final)


_EXTRACTORS: dict[str, Callable[[Path, Path, list[Path], list[str]], None]] = {
    "tar": _extract_tar,
    "zip": _extract_zip,
    "rar": _extract_rar,
    "7z": _extract_7z,
}

# Archive kinds accepted in the 'auto_extract_formats' config option.
SUPPORTED_FORMATS = frozenset(_EXTRACTORS)


def is_extractable(filename: str, enabled: Iterable[str] | None = None) -> bool:
    """Return True if filename is an archive of an enabled kind."""
    kind = archive_kind(filename)
    return kind is not None and (enabled is None or kind in set(enabled))


def _rar_volume_names(archive: Path) -> set[Path]:
    """Return all volume files belonging to the same rar set as archive."""
    name = archive.name
    match = RAR_NEW_VOLUME_RE.search(name)
    if match:
        pattern = re.compile(re.escape(name[: match.start()]) + r"\.part\d+\.rar$", re.IGNORECASE)
    else:
        pattern = re.compile(re.escape(archive.stem) + r"\.(rar|[rs]\d{2,3})$", re.IGNORECASE)
    return {p for p in archive.parent.iterdir() if pattern.fullmatch(p.name)}


def _delete_archive(archive: Path, kind: str) -> None:
    """Delete an archive after successful extraction, incl. rar set volumes."""
    paths = _rar_volume_names(archive) if kind == "rar" else {archive}
    for path in paths:
        try:
            path.unlink()
        except OSError as e:
            logger.warning("Cannot delete archive %s: %s", path, e)


def extract_recursive(
    archive_path: str | os.PathLike,
    *,
    max_depth: int = 3,
    delete_archives: bool = False,
    enabled: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Extract an archive and any archives found in the extracted output.

    Each archive is extracted in place (into its own parent directory).
    'enabled' restricts the archive kinds to unpack (see SUPPORTED_FORMATS);
    None enables all of them. Returns a dict with 'extracted' (paths
    relative to the top-level archive's directory) and 'errors'.
    """
    start = Path(archive_path)
    base_dir = start.parent
    enabled_set = set(enabled) if enabled is not None else SUPPORTED_FORMATS
    result: dict[str, Any] = {"extracted": [], "errors": []}
    if not is_extractable(start.name, enabled_set):
        return result

    seen: set[str] = set()
    current = [start]
    depth = 1
    while current and depth <= max_depth:
        discovered: list[Path] = []
        for archive in current:
            key = os.path.realpath(archive)
            if key in seen or not archive.is_file():
                continue
            seen.add(key)
            kind = archive_kind(archive.name)
            if kind is None or kind not in enabled_set:
                continue
            member_errors: list[str] = []
            try:
                new_files: list[Path] = []
                _EXTRACTORS[kind](archive, archive.parent, new_files, member_errors)
            except Exception as e:
                logger.exception("Failed to extract %s", archive)
                result["errors"].append(f"{archive.name}: {e}")
                continue
            result["errors"].extend(f"{archive.name}: {e}" for e in member_errors)
            for path in new_files:
                result["extracted"].append(os.path.relpath(path, base_dir))
                if is_extractable(path.name, enabled_set):
                    discovered.append(path)
            if delete_archives and not member_errors:
                _delete_archive(archive, kind)
        current = discovered
        depth += 1
    return result
