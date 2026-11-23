import errno
import os
import stat
import secrets
import hashlib
import time

from pathlib import Path

MAX_FILE_BYTES = 1_000_000
MAX_LIST_RESULTS = 200
MAX_SEARCH_RESULTS = 200
MAX_PATH_LENGTH = 500
WORKSPACE = Path(os.getenv("WORKSPACE", "/workspace")).resolve()

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".agent_backups",
}


def safe_path(relative_path: str = ".") -> Path:
    target = (WORKSPACE / relative_path).resolve()

    if not target.is_relative_to(WORKSPACE):
        raise ValueError("Path outside workspace is not allowed")

    return target


def _open_directory(name: str, parent_fd: int) -> int:
    return os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=parent_fd,
    )


def _open_regular_file(name: str, parent_fd: int) -> int:
    fd = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
        dir_fd=parent_fd,
    )

    try:
        info = os.fstat(fd)

        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Not a regular file")

        if info.st_size > MAX_FILE_BYTES:
            raise ValueError("File exceeds 1 MB limit")

        return fd

    except Exception:
        os.close(fd)
        raise


def _read_regular_file_bytes(name: str, parent_fd: int) -> bytes:
    fd = _open_regular_file(name, parent_fd)

    try:
        with os.fdopen(fd, "rb") as file:
            fd = -1
            content = file.read(MAX_FILE_BYTES + 1)

        if len(content) > MAX_FILE_BYTES:
            raise ValueError("File exceeds 1 MB limit")

        return content

    finally:
        if fd >= 0:
            os.close(fd)


def _read_regular_file(name: str, parent_fd: int) -> str:
    return _read_regular_file_bytes(name, parent_fd).decode(
        "utf-8",
        errors="replace",
    )


def _open_workspace() -> int:
    return os.open(
        WORKSPACE,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )


def _open_parent_directory(relative_path: str) -> tuple[int, str]:
    if not isinstance(relative_path, str):
        raise ValueError("Invalid file path")

    if not relative_path or len(relative_path) > MAX_PATH_LENGTH:
        raise ValueError("Invalid file path")

    path = Path(relative_path)

    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Invalid file path")

    parts = path.parts

    if not parts or parts == (".",):
        raise ValueError("Invalid file path")

    if any(part in ("", ".") for part in parts):
        raise ValueError("Invalid file path")

    current_fd = _open_workspace()

    try:
        for part in parts[:-1]:
            next_fd = _open_directory(part, current_fd)
            os.close(current_fd)
            current_fd = next_fd

        return current_fd, parts[-1]

    except Exception:
        os.close(current_fd)
        raise


def _atomic_create_file(
    name: str,
    parent_fd: int,
    content: bytes,
) -> None:
    temporary_name = f".agent-tmp-{secrets.token_hex(16)}"

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW

    fd = os.open(
        temporary_name,
        flags,
        0o600,
        dir_fd=parent_fd,
    )

    try:
        remaining = memoryview(content)

        while remaining:
            written = os.write(fd, remaining)

            if written <= 0:
                raise OSError("Incomplete file write")

            remaining = remaining[written:]

        os.fsync(fd)

    except Exception:
        os.close(fd)
        fd = -1
        os.unlink(temporary_name, dir_fd=parent_fd)
        raise

    finally:
        if fd >= 0:
            os.close(fd)

    try:
        # Create the final name without overwriting existing files.
        os.link(
            temporary_name,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
    finally:
        os.unlink(temporary_name, dir_fd=parent_fd)


def _atomic_replace_file(
    name: str,
    parent_fd: int,
    content: bytes,
) -> None:
    temporary_name = f".agent-tmp-{secrets.token_hex(16)}"

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW

    fd = os.open(
        temporary_name,
        flags,
        0o600,
        dir_fd=parent_fd,
    )

    try:
        remaining = memoryview(content)

        while remaining:
            written = os.write(fd, remaining)

            if written <= 0:
                raise OSError("Incomplete file write")

            remaining = remaining[written:]

        os.fsync(fd)

    finally:
        os.close(fd)

    try:
        info = os.stat(
            name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )

        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Target is not a regular file")

        os.replace(
            temporary_name,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )

    finally:
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _create_backup(
    relative_path: str,
    content: bytes,
) -> str:
    root_fd = _open_workspace()

    try:
        try:
            os.mkdir(".agent_backups", 0o700, dir_fd=root_fd)
        except FileExistsError:
            pass

        backup_fd = _open_directory(".agent_backups", root_fd)

        try:
            path_hash = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:12]

            backup_name = f"{time.time_ns()}-{path_hash}-" f"{secrets.token_hex(8)}.bak"

            _atomic_create_file(
                backup_name,
                backup_fd,
                content,
            )

            return f".agent_backups/{backup_name}"

        finally:
            os.close(backup_fd)

    finally:
        os.close(root_fd)


def write_file(
    relative_path: str,
    content: str,
    overwrite: bool = False,
) -> dict:
    if not isinstance(content, str):
        raise ValueError("Content must be a string")

    data = content.encode("utf-8")

    if len(data) > MAX_FILE_BYTES:
        raise ValueError("File exceeds 1 MB limit")

    if not isinstance(overwrite, bool):
        raise ValueError("Overwrite must be a boolean")

    parent_fd, filename = _open_parent_directory(relative_path)

    try:
        if not overwrite:
            _atomic_create_file(filename, parent_fd, data)

            return {
                "ok": True,
                "path": relative_path,
                "created": True,
                "overwritten": False,
                "backup": None,
            }

        original = _read_regular_file_bytes(filename, parent_fd)

        backup_path = _create_backup(
            relative_path,
            original,
        )

        current = _read_regular_file_bytes(filename, parent_fd)

        if current != original:
            raise RuntimeError(
                "File changed during overwrite; replacement aborted"
            )

        _atomic_replace_file(
            filename,
            parent_fd,
            data,
        )

        return {
            "ok": True,
            "path": relative_path,
            "created": False,
            "overwritten": True,
            "backup": backup_path,
        }

    finally:
        os.close(parent_fd)


def edit_file(
    relative_path: str,
    old_text: str,
    new_text: str,
) -> dict:
    if not isinstance(old_text, str) or not old_text:
        raise ValueError("Text to replace cannot be empty")

    if not isinstance(new_text, str):
        raise ValueError("Replacement text must be a string")

    if len(old_text.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("Text to replace exceeds 1 MB limit")

    if len(new_text.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("Replacement text exceeds 1 MB limit")

    parent_fd, filename = _open_parent_directory(relative_path)

    try:
        original_bytes = _read_regular_file_bytes(filename, parent_fd)

        try:
            original_text = original_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("File is not valid UTF-8 text") from exc

        occurrences = original_text.count(old_text)
        if occurrences != 1:
            raise ValueError(
                "Text to replace must match exactly once; "
                f"found {occurrences} matches"
            )

        if old_text == new_text:
            return {
                "ok": True,
                "path": relative_path,
                "changed": False,
                "backup": None,
            }

        updated_text = original_text.replace(old_text, new_text, 1)
        updated_bytes = updated_text.encode("utf-8")

        if len(updated_bytes) > MAX_FILE_BYTES:
            raise ValueError("File exceeds 1 MB limit")

        backup_path = _create_backup(relative_path, original_bytes)

        if _read_regular_file_bytes(filename, parent_fd) != original_bytes:
            raise RuntimeError("File changed during edit; replacement aborted")

        _atomic_replace_file(filename, parent_fd, updated_bytes)

        return {
            "ok": True,
            "path": relative_path,
            "changed": True,
            "backup": backup_path,
        }

    finally:
        os.close(parent_fd)


def read_file(relative_path: str) -> str:
    if not isinstance(relative_path, str) or len(relative_path) > MAX_PATH_LENGTH:
        raise ValueError("Invalid file path")

    parts = Path(relative_path).parts

    if not parts or relative_path in ("", "."):
        raise ValueError("File not found")

    if Path(relative_path).is_absolute() or ".." in parts:
        raise ValueError("Invalid file path")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    current_fd = os.open(WORKSPACE, directory_flags)

    try:
        for part in parts[:-1]:
            next_fd = os.open(
                part,
                directory_flags,
                dir_fd=current_fd,
            )

            os.close(current_fd)
            current_fd = next_fd

        return _read_regular_file(parts[-1], current_fd)

    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError("Symbolic links are not allowed") from exc

        if exc.errno == errno.ENOTDIR:
            raise ValueError("Invalid directory or symbolic link") from exc

        if exc.errno == errno.ENOENT:
            raise ValueError("File not found") from exc

        raise

    finally:
        os.close(current_fd)


def list_files(relative_path: str = ".", limit: int = 200) -> list[str]:
    if not isinstance(relative_path, str) or len(relative_path) > MAX_PATH_LENGTH:
        raise ValueError("Invalid directory path")

    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("Invalid limit")

    if not 1 <= limit <= MAX_LIST_RESULTS:
        raise ValueError("Limit must be between 1 and 200")

    if not 1 <= limit <= MAX_LIST_RESULTS:
        raise ValueError("Limit must be between 1 and 200")

    parts = Path(relative_path).parts

    if Path(relative_path).is_absolute() or ".." in parts:
        raise ValueError("Invalid directory path")

    root_fd = _open_workspace()

    try:
        current_fd = root_fd

        for part in parts:
            if part in ("", "."):
                continue

            try:
                next_fd = _open_directory(part, current_fd)
            except NotADirectoryError as exc:
                raise ValueError("Path is not a directory") from exc

            if current_fd != root_fd:
                os.close(current_fd)

            current_fd = next_fd

        results = []

        def walk(directory_fd: int, prefix: Path) -> bool:
            try:
                entries = os.scandir(directory_fd)
            except OSError:
                return False

            with entries:
                for entry in entries:
                    relative = prefix / entry.name

                    try:
                        info = entry.stat(follow_symlinks=False)

                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in IGNORED_DIRS:
                                continue

                            child_fd = _open_directory(
                                entry.name,
                                directory_fd,
                            )

                            try:
                                if walk(child_fd, relative):
                                    return True
                            finally:
                                os.close(child_fd)

                        elif entry.is_file(follow_symlinks=False):
                            if not stat.S_ISREG(info.st_mode):
                                continue

                            results.append(relative.as_posix())

                            if len(results) >= limit:
                                return True

                    except (OSError, ValueError):
                        continue

            return False

        walk(current_fd, Path(relative_path))

        return results

    finally:
        if current_fd != root_fd:
            os.close(current_fd)

        os.close(root_fd)


def search_code(
    query: str,
    relative_path: str = ".",
    limit: int = 50,
) -> list[dict]:
    if not isinstance(query, str) or not query:
        raise ValueError("Search query cannot be empty")

    if not isinstance(relative_path, str) or len(relative_path) > MAX_PATH_LENGTH:
        raise ValueError("Invalid directory path")

    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("Invalid limit")

    if not 1 <= limit <= MAX_SEARCH_RESULTS:
        raise ValueError("Limit must be between 1 and 200")

    parts = Path(relative_path).parts

    if Path(relative_path).is_absolute() or ".." in parts:
        raise ValueError("Invalid directory path")

    if parts and relative_path not in ("", "."):
        parent_fd, filename = _open_parent_directory(relative_path)

        try:
            info = os.stat(
                filename,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )

            if stat.S_ISREG(info.st_mode):
                if info.st_size > MAX_FILE_BYTES:
                    return []

                content = _read_regular_file(filename, parent_fd)
                return [
                    {
                        "file": Path(relative_path).as_posix(),
                        "line": line_number,
                        "content": line.strip()[:500],
                    }
                    for line_number, line in enumerate(content.splitlines(), 1)
                    if query.casefold() in line.casefold()
                ][:limit]

            if stat.S_ISLNK(info.st_mode):
                raise ValueError("Symbolic links are not allowed")
        finally:
            os.close(parent_fd)

    root_fd = _open_workspace()
    current_fd = root_fd

    try:
        for part in parts:
            if part in ("", "."):
                continue

            try:
                next_fd = _open_directory(part, current_fd)
            except NotADirectoryError as exc:
                raise ValueError("Path is not a directory") from exc

            if current_fd != root_fd:
                os.close(current_fd)

            current_fd = next_fd

        results = []

        def walk(directory_fd: int, prefix: Path) -> bool:
            try:
                entries = os.scandir(directory_fd)
            except OSError:
                return False

            with entries:
                for entry in entries:
                    relative = prefix / entry.name

                    try:
                        info = entry.stat(follow_symlinks=False)

                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in IGNORED_DIRS:
                                continue

                            child_fd = _open_directory(
                                entry.name,
                                directory_fd,
                            )

                            try:
                                if walk(child_fd, relative):
                                    return True
                            finally:
                                os.close(child_fd)

                        elif entry.is_file(follow_symlinks=False):
                            if info.st_size > MAX_FILE_BYTES:
                                continue

                            content = _read_regular_file(
                                entry.name,
                                directory_fd,
                            )

                            for line_number, line in enumerate(content.splitlines(), 1):
                                if query.casefold() in line.casefold():
                                    results.append(
                                        {
                                            "file": relative.as_posix(),
                                            "line": line_number,
                                            "content": line.strip()[:500],
                                        }
                                    )

                                    if len(results) >= limit:
                                        return True

                    except (OSError, ValueError, UnicodeError):
                        continue

            return False

        walk(current_fd, Path(relative_path))
        return results

    finally:
        if current_fd != root_fd:
            os.close(current_fd)

        os.close(root_fd)
