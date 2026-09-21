"""In-memory stand-in for ``paramiko.SFTPClient`` covering the calls
``api.services.file_transfer`` and ``discover_sftp_files`` make."""
from __future__ import annotations

import errno
import io
import posixpath
import stat as stat_module
from types import SimpleNamespace


class FakeSFTP:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/"}
        self.posix_rename_supported = True
        self.fail_putfo_after_partial = False
        self.calls: list[str] = []
        self.posix_rename_error: Exception | None = None
        self.rename_error: Exception | None = None

    # -- discovery ----------------------------------------------------------
    def listdir_attr(self, path: str):
        self.calls.append("listdir_attr")
        if path != "/" and path not in self.dirs:
            raise IOError(errno.ENOENT, path)
        prefix = path.rstrip("/") + "/"
        entries = []
        for file_path in sorted(self.files):
            rest = file_path[len(prefix):] if file_path.startswith(prefix) else None
            if rest and "/" not in rest:
                entries.append(SimpleNamespace(filename=rest, st_mode=stat_module.S_IFREG | 0o644))
        for dir_path in sorted(self.dirs):
            rest = dir_path[len(prefix):] if dir_path.startswith(prefix) else None
            if rest and "/" not in rest:
                entries.append(SimpleNamespace(filename=rest, st_mode=stat_module.S_IFDIR | 0o755))
        return entries

    # -- reads --------------------------------------------------------------
    def open(self, path: str, mode: str = "rb"):
        if path not in self.files:
            raise IOError(errno.ENOENT, path)
        return io.BytesIO(self.files[path])

    def stat(self, path: str):
        self.calls.append("stat")
        if path in self.files:
            return SimpleNamespace(st_size=len(self.files[path]))
        if path in self.dirs:
            return SimpleNamespace(st_size=0)
        raise IOError(errno.ENOENT, path)

    # -- writes -------------------------------------------------------------
    def mkdir(self, path: str) -> None:
        if path in self.dirs:
            raise IOError("Failure")
        self.dirs.add(path)

    def putfo(self, fl, remotepath: str, file_size: int = 0, callback=None, confirm: bool = True):
        if posixpath.dirname(remotepath) not in self.dirs:
            raise IOError(errno.ENOENT, remotepath)
        data = b""
        while True:
            chunk = fl.read(32768)
            if not chunk:
                break
            data += chunk
            if self.fail_putfo_after_partial:
                self.files[remotepath] = data
                raise IOError("connection reset")
        self.files[remotepath] = data
        return SimpleNamespace(st_size=len(data))

    def posix_rename(self, old: str, new: str) -> None:
        self.calls.append("posix_rename")
        if self.posix_rename_error is not None:
            raise self.posix_rename_error
        if not self.posix_rename_supported:
            raise IOError("Operation unsupported")
        self.files[new] = self.files.pop(old)

    def rename(self, old: str, new: str) -> None:
        self.calls.append("rename")
        if self.rename_error is not None:
            raise self.rename_error
        if new in self.files:
            raise IOError("Failure")
        self.files[new] = self.files.pop(old)

    def remove(self, path: str) -> None:
        self.calls.append("remove")
        if path not in self.files:
            raise IOError(errno.ENOENT, path)
        del self.files[path]

    def close(self) -> None:
        pass
