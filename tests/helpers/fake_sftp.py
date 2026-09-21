"""In-memory stand-in for ``paramiko.SFTPClient`` covering the calls
``api.services.file_transfer`` and ``discover_sftp_files`` make."""
from __future__ import annotations

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

    # -- discovery ----------------------------------------------------------
    def listdir_attr(self, path: str):
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
            raise FileNotFoundError(path)
        return io.BytesIO(self.files[path])

    def stat(self, path: str):
        if path in self.files:
            return SimpleNamespace(st_size=len(self.files[path]))
        if path in self.dirs:
            return SimpleNamespace(st_size=0)
        raise FileNotFoundError(path)

    # -- writes -------------------------------------------------------------
    def mkdir(self, path: str) -> None:
        self.dirs.add(path)

    def putfo(self, fl, remotepath: str, callback=None, confirm: bool = True):
        assert posixpath.dirname(remotepath) in self.dirs, f"parent directory missing for {remotepath}"
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
        if not self.posix_rename_supported:
            raise IOError("Operation unsupported")
        self.files[new] = self.files.pop(old)

    def rename(self, old: str, new: str) -> None:
        if new in self.files:
            raise IOError("Failure")
        self.files[new] = self.files.pop(old)

    def remove(self, path: str) -> None:
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]

    def close(self) -> None:
        pass
