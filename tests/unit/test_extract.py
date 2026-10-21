"""Unit tests for archive extraction helpers."""

import io
import tarfile
import zipfile
from pathlib import Path
from typing import Literal

import py7zr
import pytest

from dccbot.extract import archive_kind, extract_recursive, is_extractable


def _make_tar(path: Path, members: dict[str, bytes], *, mode: Literal["w", "w:gz"] = "w") -> Path:
    """Create a tar archive containing the given name->content members."""
    with tarfile.open(path, mode) as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _make_zip(path: Path, members: dict[str, bytes]) -> Path:
    """Create a zip archive containing the given name->content members."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("pack.tar", "tar"),
        ("pack.tar.gz", "tar"),
        ("pack.tgz", "tar"),
        ("pack.tar.bz2", "tar"),
        ("pack.tar.xz", "tar"),
        ("pack.zip", "zip"),
        ("pack.rar", "rar"),
        ("pack.part01.rar", "rar"),
        ("pack.part1.rar", "rar"),
        ("pack.part02.rar", None),
        ("pack.r00", None),
        ("pack.s01", None),
        ("pack.7z", "7z"),
        ("pack.7z.001", None),
        ("pack.7z.002", None),
        ("movie.mkv", None),
        ("noext", None),
        ("PACK.TAR.GZ", "tar"),
        ("PACK.ZIP", "zip"),
        ("PACK.RAR", "rar"),
    ],
)
def test_archive_kind(filename, expected):
    """Filenames classify into archive kinds; volumes/non-archives return None."""
    assert archive_kind(filename) == expected


def test_extract_tar(tmp_path):
    """A plain tar archive extracts its members in place."""
    archive = _make_tar(tmp_path / "pack.tar", {"a.txt": b"aaa", "sub/b.txt": b"bb"})
    result = extract_recursive(archive)
    assert result["errors"] == []
    assert sorted(result["extracted"]) == ["a.txt", "sub/b.txt"]
    assert (tmp_path / "a.txt").read_bytes() == b"aaa"
    assert (tmp_path / "sub" / "b.txt").read_bytes() == b"bb"


def test_extract_tar_gz(tmp_path):
    """A gzipped tar is auto-detected and extracted."""
    archive = _make_tar(tmp_path / "pack.tar.gz", {"a.txt": b"data"}, mode="w:gz")
    result = extract_recursive(archive)
    assert result["errors"] == []
    assert result["extracted"] == ["a.txt"]


def test_extract_zip(tmp_path):
    """A zip archive extracts its members in place."""
    archive = _make_zip(tmp_path / "pack.zip", {"x.txt": b"xx"})
    result = extract_recursive(archive)
    assert result["errors"] == []
    assert result["extracted"] == ["x.txt"]
    assert (tmp_path / "x.txt").read_bytes() == b"xx"


def test_extract_nested_archive(tmp_path):
    """An archive inside an extracted archive is extracted recursively."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep.txt", b"deep")
    outer = _make_tar(tmp_path / "outer.tar", {"inner.zip": inner.getvalue()})
    result = extract_recursive(outer)
    assert result["errors"] == []
    assert sorted(result["extracted"]) == ["deep.txt", "inner.zip"]
    assert (tmp_path / "deep.txt").read_bytes() == b"deep"


def test_extract_max_depth(tmp_path):
    """Nesting beyond max_depth leaves deeper archives unextracted."""
    level2 = io.BytesIO()
    with zipfile.ZipFile(level2, "w") as zf:
        zf.writestr("too_deep.txt", b"x")
    level1 = io.BytesIO()
    with zipfile.ZipFile(level1, "w") as zf:
        zf.writestr("level2.zip", level2.getvalue())

    shallow_dir = tmp_path / "shallow"
    deep_dir = tmp_path / "deep"
    shallow_dir.mkdir()
    deep_dir.mkdir()
    top_shallow = _make_zip(shallow_dir / "top.zip", {"level1.zip": level1.getvalue()})
    top_deep = _make_zip(deep_dir / "top.zip", {"level1.zip": level1.getvalue()})

    shallow = extract_recursive(top_shallow, max_depth=1)
    assert shallow["errors"] == []
    assert shallow["extracted"] == ["level1.zip"]
    assert not (shallow_dir / "level2.zip").exists()

    deep = extract_recursive(top_deep, max_depth=3)
    assert deep["errors"] == []
    assert sorted(deep["extracted"]) == ["level1.zip", "level2.zip", "too_deep.txt"]


def test_extract_skips_path_traversal(tmp_path):
    """Members with absolute or parent-escaping paths are skipped."""
    archive = tmp_path / "evil.tar"
    with tarfile.open(archive, "w") as tf:
        for name in ("../escape.txt", "/tmp/abs.txt", "ok/../still_inside.txt"):
            data = b"bad"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        good = tarfile.TarInfo("good.txt")
        good.size = 4
        tf.addfile(good, io.BytesIO(b"good"))

    result = extract_recursive(archive)
    assert result["errors"] == []
    assert not (tmp_path.parent / "escape.txt").exists()
    assert not (tmp_path / ".." / "escape.txt").resolve().exists()
    assert "good.txt" in result["extracted"]
    # 'ok/../still_inside.txt' resolves to still_inside.txt inside dest dir.
    assert (tmp_path / "still_inside.txt").exists()


def test_extract_collision_uses_unique_name(tmp_path):
    """Existing files are not overwritten; a numeric suffix is added."""
    (tmp_path / "a.txt").write_bytes(b"original")
    archive = _make_zip(tmp_path / "pack.zip", {"a.txt": b"new"})
    result = extract_recursive(archive)
    assert result["errors"] == []
    assert result["extracted"] == ["a.1.txt"]
    assert (tmp_path / "a.txt").read_bytes() == b"original"
    assert (tmp_path / "a.1.txt").read_bytes() == b"new"


def test_extract_delete_archives(tmp_path):
    """delete_archives removes the archive after successful extraction."""
    archive = _make_zip(tmp_path / "pack.zip", {"x.txt": b"xx"})
    result = extract_recursive(archive, delete_archives=True)
    assert result["errors"] == []
    assert not archive.exists()
    assert (tmp_path / "x.txt").exists()


def test_extract_member_failure_does_not_abort_archive(tmp_path):
    """A member that collides with an existing file is skipped with an
    error while the remaining members are still extracted."""
    (tmp_path / "sub").write_bytes(b"i am a file, not a dir")
    archive = _make_zip(tmp_path / "pack.zip", {"sub/": b"", "sub/b.txt": b"bb", "ok.txt": b"ok"})
    result = extract_recursive(archive)
    assert "ok.txt" in result["extracted"]
    assert (tmp_path / "ok.txt").read_bytes() == b"ok"
    assert result["errors"]
    assert any("sub" in e for e in result["errors"])


def test_extract_member_failure_keeps_archive(tmp_path):
    """delete_archives keeps the archive when some members failed, so the
    failed content is not lost."""
    (tmp_path / "sub").write_bytes(b"i am a file, not a dir")
    archive = _make_zip(tmp_path / "pack.zip", {"sub/b.txt": b"bb", "ok.txt": b"ok"})
    result = extract_recursive(archive, delete_archives=True)
    assert "ok.txt" in result["extracted"]
    assert result["errors"]
    assert archive.exists()


def test_extract_keeps_archive_on_failure(tmp_path):
    """A corrupt archive is not deleted and reports an error."""
    archive = tmp_path / "broken.tar"
    archive.write_bytes(b"not a tar at all")
    result = extract_recursive(archive, delete_archives=True)
    assert result["errors"]
    assert result["extracted"] == []
    assert archive.exists()


def test_extract_non_archive_is_noop(tmp_path):
    """Non-archive files are ignored."""
    plain = tmp_path / "movie.mkv"
    plain.write_bytes(b"video")
    result = extract_recursive(plain)
    assert result == {"extracted": [], "errors": []}


def test_extract_tar_skips_links_and_devices(tmp_path):
    """Symlinks, hardlinks and device members are not extracted."""
    archive = tmp_path / "links.tar"
    with tarfile.open(archive, "w") as tf:
        sym = tarfile.TarInfo("sym.txt")
        sym.type = tarfile.SYMTYPE
        sym.linkname = "/etc/passwd"
        tf.addfile(sym)
        hard = tarfile.TarInfo("hard.txt")
        hard.type = tarfile.LNKTYPE
        hard.linkname = "good.txt"
        tf.addfile(hard)
        good = tarfile.TarInfo("good.txt")
        good.size = 4
        tf.addfile(good, io.BytesIO(b"good"))

    result = extract_recursive(archive)
    assert result["errors"] == []
    assert result["extracted"] == ["good.txt"]
    assert not (tmp_path / "sym.txt").exists()


def test_rar_volume_siblings_found(tmp_path):
    """Volume detection covers new-style part names and old-style suffixes."""
    from dccbot.extract import _rar_volume_names

    for name in ("set.part01.rar", "set.part02.rar", "set.rar", "set.r00", "set.r01", "other.txt"):
        (tmp_path / name).write_bytes(b"x")

    new_style = _rar_volume_names(tmp_path / "set.part01.rar")
    assert new_style == {tmp_path / "set.part01.rar", tmp_path / "set.part02.rar"}

    old_style = _rar_volume_names(tmp_path / "set.rar")
    assert old_style == {tmp_path / "set.rar", tmp_path / "set.r00", tmp_path / "set.r01"}


def _make_7z(path: Path, members: dict[str, bytes]) -> Path:
    """Create a 7z archive containing the given name->content members."""
    with py7zr.SevenZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(data, name)
    return path


def test_extract_7z(tmp_path):
    """A 7z archive extracts its members in place."""
    archive = _make_7z(tmp_path / "pack.7z", {"a.txt": b"aaa", "sub/b.txt": b"bb"})
    result = extract_recursive(archive)
    assert result["errors"] == []
    assert sorted(result["extracted"]) == ["a.txt", "sub/b.txt"]
    assert (tmp_path / "a.txt").read_bytes() == b"aaa"
    assert (tmp_path / "sub" / "b.txt").read_bytes() == b"bb"


def test_extract_7z_collision(tmp_path):
    """7z members that collide with existing files get unique names."""
    (tmp_path / "a.txt").write_bytes(b"original")
    archive = _make_7z(tmp_path / "pack.7z", {"a.txt": b"new"})
    result = extract_recursive(archive)
    assert result["errors"] == []
    assert result["extracted"] == ["a.1.txt"]
    assert (tmp_path / "a.txt").read_bytes() == b"original"


def test_extract_7z_nested_in_zip(tmp_path):
    """A 7z nested inside a zip is extracted recursively."""
    inner = io.BytesIO()
    with py7zr.SevenZipFile(inner, "w") as zf:
        zf.writestr(b"deep", "deep.txt")
    outer = _make_zip(tmp_path / "outer.zip", {"inner.7z": inner.getvalue()})
    result = extract_recursive(outer)
    assert result["errors"] == []
    assert sorted(result["extracted"]) == ["deep.txt", "inner.7z"]


def test_extract_enabled_formats_restricts_top_level(tmp_path):
    """A top-level archive of a disabled kind is left untouched."""
    archive = _make_zip(tmp_path / "pack.zip", {"x.txt": b"xx"})
    result = extract_recursive(archive, enabled=["tar"])
    assert result == {"extracted": [], "errors": []}
    assert not (tmp_path / "x.txt").exists()


def test_extract_enabled_formats_restricts_nested(tmp_path):
    """Nested archives of disabled kinds stay packed; enabled kinds unpack."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep.txt", b"deep")
    outer = _make_tar(tmp_path / "outer.tar", {"inner.zip": inner.getvalue(), "plain.txt": b"p"})
    result = extract_recursive(outer, enabled=["tar"])
    assert result["errors"] == []
    # The zip was extracted as a file but not unpacked itself.
    assert sorted(result["extracted"]) == ["inner.zip", "plain.txt"]
    assert not (tmp_path / "deep.txt").exists()


@pytest.mark.parametrize(
    ("filename", "enabled", "expected"),
    [
        ("pack.tar", None, True),
        ("pack.tar", ["tar", "zip"], True),
        ("pack.tar", ["zip"], False),
        ("pack.7z", ["7z"], True),
        ("pack.rar", ["7z"], False),
        ("pack.zip", [], False),
        ("movie.mkv", None, False),
    ],
)
def test_is_extractable(filename, enabled, expected):
    """is_extractable honours the enabled-format list."""
    assert is_extractable(filename, enabled) == expected


def test_extract_flatten_tar(tmp_path):
    """Flatten drops the archive's directory structure into the root."""
    archive = _make_tar(tmp_path / "pack.tar", {"Movie/film.mkv": b"vid", "a.txt": b"aaa"})
    result = extract_recursive(archive, flatten=True)
    assert result["errors"] == []
    assert sorted(result["extracted"]) == ["a.txt", "film.mkv"]
    assert (tmp_path / "film.mkv").read_bytes() == b"vid"
    assert not (tmp_path / "Movie").exists()


def test_extract_flatten_zip(tmp_path):
    """Flatten lands nested zip members directly in the root."""
    archive = _make_zip(tmp_path / "pack.zip", {"sub/deep/file.txt": b"deep"})
    result = extract_recursive(archive, flatten=True)
    assert result["errors"] == []
    assert result["extracted"] == ["file.txt"]
    assert (tmp_path / "file.txt").read_bytes() == b"deep"
    assert not (tmp_path / "sub").exists()


def test_extract_flatten_7z(tmp_path):
    """Flatten lands nested 7z members directly in the root."""
    archive = _make_7z(tmp_path / "pack.7z", {"sub/b.txt": b"bb"})
    result = extract_recursive(archive, flatten=True)
    assert result["errors"] == []
    assert result["extracted"] == ["b.txt"]
    assert (tmp_path / "b.txt").read_bytes() == b"bb"
    assert not (tmp_path / "sub").exists()


def test_extract_flatten_collision(tmp_path):
    """Same-named files from different subdirs get unique names at the root."""
    archive = _make_zip(tmp_path / "pack.zip", {"a/dup.txt": b"one", "b/dup.txt": b"two"})
    result = extract_recursive(archive, flatten=True)
    assert result["errors"] == []
    assert sorted(result["extracted"]) == ["dup.1.txt", "dup.txt"]
    contents = sorted((tmp_path / n).read_bytes() for n in ("dup.txt", "dup.1.txt"))
    assert contents == [b"one", b"two"]


def test_extract_flatten_nested_archive(tmp_path):
    """A nested archive inside a subdir extracts its contents to the root too."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep/movie.mkv", b"vid")
    outer = _make_tar(tmp_path / "outer.tar", {"pack/inner.zip": inner.getvalue()})
    result = extract_recursive(outer, flatten=True)
    assert result["errors"] == []
    assert sorted(result["extracted"]) == ["inner.zip", "movie.mkv"]
    assert (tmp_path / "movie.mkv").read_bytes() == b"vid"
    assert not (tmp_path / "pack" / "deep").exists()


def test_extract_flatten_defuses_traversal(tmp_path):
    """Flattened extraction turns traversal members into plain basenames."""
    archive = tmp_path / "evil.tar"
    with tarfile.open(archive, "w") as tf:
        data = b"bad"
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    result = extract_recursive(archive, flatten=True)
    assert result["errors"] == []
    assert not (tmp_path.parent / "escape.txt").exists()
    assert (tmp_path / "escape.txt").read_bytes() == b"bad"


def test_extract_flatten_leaves_no_subdirs(tmp_path):
    """Flattened output never creates archive subdirectories at all."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("file.txt", b"f")
    outer = _make_zip(tmp_path / "outer.zip", {"pack/inner.zip": inner.getvalue(), "top.txt": b"t"})
    result = extract_recursive(outer, flatten=True, delete_archives=True)
    assert result["errors"] == []
    assert (tmp_path / "file.txt").exists()
    assert (tmp_path / "top.txt").exists()
    assert not (tmp_path / "pack").exists()
    assert not (tmp_path / "inner.zip").exists()
    assert not (tmp_path / "outer.zip").exists()
    # The download dir contains only the flattened output - no subdirs.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["file.txt", "top.txt"]
