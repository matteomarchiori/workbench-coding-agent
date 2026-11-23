import os
import pytest

from agent.tools import filesystem


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(filesystem, "WORKSPACE", root.resolve())
    return root


def test_safe_path_accepts_relative_path(workspace):
    (workspace / "result.txt").write_text("hello")

    result = filesystem.safe_path("result.txt")

    assert result == (workspace / "result.txt").resolve()


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "../../etc/passwd",
        "/etc/passwd",
    ],
)
def test_safe_path_rejects_paths_outside_workspace(workspace, path):
    with pytest.raises(ValueError, match="outside workspace"):
        filesystem.safe_path(path)


def test_read_file_reads_workspace_file(workspace):
    (workspace / "result.txt").write_text("hello")

    assert filesystem.read_file("result.txt") == "hello"


def test_read_file_rejects_missing_file(workspace):
    with pytest.raises(ValueError, match="File not found"):
        filesystem.read_file("missing.txt")


def test_read_file_rejects_file_over_1mb(workspace):
    (workspace / "large.txt").write_text("x" * 1_000_001)

    with pytest.raises(ValueError, match="exceeds 1 MB"):
        filesystem.read_file("large.txt")


def test_safe_path_rejects_symlink_escape(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")

    (workspace / "external").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside workspace"):
        filesystem.safe_path("external/secret.txt")


def test_list_files_skips_symlink_files_and_directories(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")

    (workspace / "normal.txt").write_text("normal")
    (workspace / "linked.txt").symlink_to(outside / "secret.txt")
    (workspace / "external").symlink_to(outside, target_is_directory=True)

    results = filesystem.list_files()

    assert "normal.txt" in results
    assert "linked.txt" not in results
    assert not any("secret.txt" in item for item in results)


def test_search_code_skips_symlink_files(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("needle")

    (workspace / "normal.txt").write_text("needle")
    (workspace / "linked.txt").symlink_to(outside / "secret.txt")

    results = filesystem.search_code("needle")

    assert len(results) == 1
    assert results[0]["file"] == "normal.txt"


def test_search_code_searches_a_specific_file(workspace):
    (workspace / "README.md").write_text("Title\nNeedle here\nneedle again\n")

    assert filesystem.search_code("needle", "README.md") == [
        {"file": "README.md", "line": 2, "content": "Needle here"},
        {"file": "README.md", "line": 3, "content": "needle again"},
    ]


def test_list_files_rejects_non_directory(workspace):
    (workspace / "file.txt").write_text("hello")

    with pytest.raises(ValueError, match="not a directory"):
        filesystem.list_files("file.txt")


def test_search_code_rejects_empty_query(workspace):
    with pytest.raises(ValueError, match="cannot be empty"):
        filesystem.search_code("")

def test_read_file_rejects_internal_symlink(workspace):
    (workspace / "original.txt").write_text("secret")
    (workspace / "link.txt").symlink_to(workspace / "original.txt")

    with pytest.raises(ValueError, match="Symbolic links"):
        filesystem.read_file("link.txt")


def test_read_file_rejects_external_symlink(workspace, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")

    (workspace / "link.txt").symlink_to(outside)

    with pytest.raises(ValueError, match="Symbolic links"):
        filesystem.read_file("link.txt")


def test_read_file_rejects_symlink_directory(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")

    (workspace / "external").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(
        ValueError,
        match="Symbolic links|Invalid directory",
    ):
        filesystem.read_file("external/secret.txt")


def test_read_file_rejects_parent_traversal(workspace, tmp_path):
    with pytest.raises(ValueError, match="Invalid file path"):
        filesystem.read_file("../secret.txt")

def test_list_files_ignores_internal_symlink(workspace):
    (workspace / "original.txt").write_text("hello")
    (workspace / "link.txt").symlink_to(workspace / "original.txt")

    results = filesystem.list_files()

    assert "original.txt" in results
    assert "link.txt" not in results


def test_list_files_ignores_internal_symlink_directory(workspace):
    real_dir = workspace / "real"
    real_dir.mkdir()
    (real_dir / "file.txt").write_text("hello")

    (workspace / "link").symlink_to(
        real_dir,
        target_is_directory=True,
    )

    results = filesystem.list_files()

    assert "real/file.txt" in results
    assert "link/file.txt" not in results


def test_search_code_ignores_internal_symlink(workspace):
    (workspace / "original.txt").write_text("needle")
    (workspace / "link.txt").symlink_to(workspace / "original.txt")

    results = filesystem.search_code("needle")

    assert len(results) == 1
    assert results[0]["file"] == "original.txt"


def test_search_code_ignores_internal_symlink_directory(workspace):
    real_dir = workspace / "real"
    real_dir.mkdir()
    (real_dir / "file.txt").write_text("needle")

    (workspace / "link").symlink_to(
        real_dir,
        target_is_directory=True,
    )

    results = filesystem.search_code("needle")

    assert len(results) == 1
    assert results[0]["file"] == "real/file.txt"

def test_list_files_skips_unreadable_directory(workspace, monkeypatch):
    (workspace / "blocked").mkdir()
    (workspace / "visible.txt").write_text("hello")

    original_scandir = filesystem.os.scandir

    def fake_scandir(path):
        if path == workspace / "blocked":
            raise PermissionError("Permission denied")
        return original_scandir(path)

    monkeypatch.setattr(filesystem.os, "scandir", fake_scandir)

    results = filesystem.list_files()

    assert "visible.txt" in results
    assert "blocked/file.txt" not in results


def test_search_code_skips_unreadable_directory(workspace, monkeypatch):
    (workspace / "blocked").mkdir()
    (workspace / "visible.txt").write_text("needle")

    original_scandir = filesystem.os.scandir

    def fake_scandir(path):
        if path == workspace / "blocked":
            raise PermissionError("Permission denied")
        return original_scandir(path)

    monkeypatch.setattr(filesystem.os, "scandir", fake_scandir)

    results = filesystem.search_code("needle")

    assert len(results) == 1
    assert results[0]["file"] == "visible.txt"

def test_atomic_create_file_creates_file(workspace):
    parent_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)

    try:
        filesystem._atomic_create_file(
            "new.txt",
            parent_fd,
            b"hello world",
        )
    finally:
        os.close(parent_fd)

    assert (workspace / "new.txt").read_bytes() == b"hello world"


def test_atomic_create_file_does_not_overwrite(workspace):
    target = workspace / "existing.txt"
    target.write_text("original")

    parent_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)

    try:
        with pytest.raises(FileExistsError):
            filesystem._atomic_create_file(
                "existing.txt",
                parent_fd,
                b"replacement",
            )
    finally:
        os.close(parent_fd)

    assert target.read_text() == "original"


def test_atomic_create_file_removes_temporary_file(workspace):
    parent_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)

    try:
        filesystem._atomic_create_file(
            "new.txt",
            parent_fd,
            b"hello",
        )
    finally:
        os.close(parent_fd)

    temporary_files = list(workspace.glob(".agent-tmp-*"))

    assert temporary_files == []

def test_write_file_creates_file(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", workspace)

    result = filesystem.write_file("hello.txt", "Ciao agente!")

    assert result["ok"] is True
    assert result["created"] is True
    assert (workspace / "hello.txt").read_text() == "Ciao agente!"


def test_write_file_with_overwrite_creates_missing_file(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", workspace)

    result = filesystem.write_file(
        "CONTRIBUTING.md",
        "# Contributing",
        overwrite=True,
    )

    assert result == {
        "ok": True,
        "path": "CONTRIBUTING.md",
        "created": True,
        "overwritten": False,
        "backup": None,
    }
    assert (workspace / "CONTRIBUTING.md").read_text() == "# Contributing"


def test_write_file_does_not_overwrite(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", workspace)

    target = workspace / "hello.txt"
    target.write_text("Originale")

    with pytest.raises(FileExistsError):
        filesystem.write_file("hello.txt", "New content")

    assert target.read_text() == "Originale"


def test_edit_file_replaces_unique_text_and_creates_backup(workspace):
    target = workspace / "README.md"
    target.write_text("# Guide\n\nOld section\n")

    result = filesystem.edit_file(
        "README.md",
        "Old section",
        "New section",
    )

    assert result["ok"] is True
    assert result["path"] == "README.md"
    assert result["changed"] is True
    assert isinstance(result["backup"], str)
    assert target.read_text() == "# Guide\n\nNew section\n"
    assert (workspace / result["backup"]).read_text() == "# Guide\n\nOld section\n"


def test_edit_file_rejects_non_unique_text(workspace):
    target = workspace / "README.md"
    target.write_text("Repeat\n\nRepeat\n")

    with pytest.raises(ValueError, match="exactly once"):
        filesystem.edit_file("README.md", "Repeat", "Updated")

    assert target.read_text() == "Repeat\n\nRepeat\n"
    assert not (workspace / ".agent_backups").exists()


def test_edit_file_noop_does_not_create_backup(workspace):
    target = workspace / "README.md"
    target.write_text("Existing section\n")

    result = filesystem.edit_file(
        "README.md",
        "Existing section",
        "Existing section",
    )

    assert result["changed"] is False
    assert result["backup"] is None
    assert target.read_text() == "Existing section\n"
    assert not (workspace / ".agent_backups").exists()


def test_write_file_rejects_oversized_content(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", workspace)

    with pytest.raises(ValueError, match="1 MB"):
        filesystem.write_file("large.txt", "x" * 1_000_001)


def test_write_file_rejects_invalid_overwrite(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", workspace)

    with pytest.raises(ValueError, match="boolean"):
        filesystem.write_file("hello.txt", "test", overwrite="yes")

def test_create_backup_preserves_original_content(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", workspace)

    backup_path = filesystem._create_backup(
        "src/example.py",
        b"original content",
    )

    backup_file = workspace / backup_path

    assert backup_file.exists()
    assert backup_file.read_bytes() == b"original content"
    assert backup_path.startswith(".agent_backups/")

def test_atomic_replace_file_replaces_content(workspace):
    target = workspace / "example.txt"
    target.write_text("original")

    parent_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)

    try:
        filesystem._atomic_replace_file(
            "example.txt",
            parent_fd,
            b"replacement",
        )
    finally:
        os.close(parent_fd)

    assert target.read_text() == "replacement"


def test_atomic_replace_file_rejects_symlink(workspace):
    target = workspace / "outside.txt"
    target.write_text("protected")

    link = workspace / "example.txt"
    link.symlink_to(target)

    parent_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)

    try:
        with pytest.raises(ValueError, match="regular file"):
            filesystem._atomic_replace_file(
                "example.txt",
                parent_fd,
                b"replacement",
            )
    finally:
        os.close(parent_fd)

    assert target.read_text() == "protected"
    assert link.is_symlink()

def test_write_file_overwrites_and_creates_backup(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", workspace)

    target = workspace / "example.txt"
    target.write_text("original content")

    result = filesystem.write_file(
        "example.txt",
        "new content",
        overwrite=True,
    )

    assert result["ok"] is True
    assert result["created"] is False
    assert result["overwritten"] is True

    assert target.read_text() == "new content"

    backup = workspace / result["backup"]

    assert backup.exists()
    assert backup.read_bytes() == b"original content"


def test_write_file_does_not_modify_file_without_overwrite(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", workspace)

    target = workspace / "example.txt"
    target.write_text("original content")

    with pytest.raises(FileExistsError):
        filesystem.write_file(
            "example.txt",
            "new content",
            overwrite=False,
        )

    assert target.read_text() == "original content"
    assert not (workspace / ".agent_backups").exists()

def test_write_file_backup_preserves_non_utf8_bytes(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", workspace)

    target = workspace / "binary.txt"
    original = b"\x80\xff\xfe"
    target.write_bytes(original)

    result = filesystem.write_file(
        "binary.txt",
        "replacement",
        overwrite=True,
    )

    backup = workspace / result["backup"]

    assert backup.read_bytes() == original
    assert target.read_text() == "replacement"

def test_write_file_detects_change_before_replace(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", workspace)

    target = workspace / "example.txt"
    target.write_text("original")

    original_read = filesystem._read_regular_file_bytes
    calls = 0

    def modified_read(name, parent_fd):
        nonlocal calls
        calls += 1

        if calls == 2:
            return b"external modification"

        return original_read(name, parent_fd)

    monkeypatch.setattr(
        filesystem,
        "_read_regular_file_bytes",
        modified_read,
    )

    with pytest.raises(RuntimeError, match="changed during overwrite"):
        filesystem.write_file(
            "example.txt",
            "replacement",
            overwrite=True,
        )

    assert target.read_text() == "original"