"""Serve tests/helpers/fake_crushftp.py on the Docker network, loaded with the
files the seed step wrote. It reuses the fake's HTTP handler, so ATOM's real
CrushFTP client talks to it exactly as it does in the unit tests."""
from __future__ import annotations

import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from tests.helpers.fake_crushftp import FakeCrushFtp, _Handler


def load_files(fake: FakeCrushFtp, root: Path) -> None:
    for path in sorted(root.rglob("*")):
        remote = "/" + path.relative_to(root).as_posix()
        if path.is_dir():
            fake.dirs.add(remote)
        else:
            fake.files[remote] = path.read_bytes()


def main() -> None:
    fake = FakeCrushFtp(
        username=os.environ["CRUSHFTP_SIM_USER"],
        password=os.environ["CRUSHFTP_SIM_PASSWORD"],
    )
    root = Path(os.environ["CRUSHFTP_SIM_ROOT"])
    load_files(fake, root)

    class Handler(_Handler):
        server_state = fake

    # Files dropped in later, like the demo's arrival signal, appear as they would on a server.
    def keep_loading() -> None:
        while True:
            time.sleep(1)
            load_files(fake, root)

    threading.Thread(target=keep_loading, daemon=True).start()

    port = int(os.environ.get("CRUSHFTP_SIM_PORT", "8080"))
    print(f"CrushFTP simulator serving {len(fake.files)} file(s) on port {port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
