"""In-process stand-in for a CrushFTP server's HTTP API, covering the calls
``api.services.crushftp_client`` makes. Behaviour follows what was observed on
CrushFTP 11.5.5 (see section 10 of
docs/superpowers/specs/2026-10-10-crushftp-file-server-design.md), including
the awkward parts: ``+`` is not decoded as a space in a form body, an
unauthenticated command answers 404, and a rename never replaces a file."""
from __future__ import annotations

import hashlib
import json
import posixpath
import secrets
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, quote, unquote, urlsplit

_FUNCTION_PATH = "/WebInterface/function/"
_MUTATING = {"makedir", "rename", "delete"}


class FakeCrushFtp:
    def __init__(
        self,
        username: str = "atom",
        password: str = "atompass",
        *,
        api_key_header: str | None = None,
        api_key: str | None = None,
        certfile: str | None = None,
        keyfile: str | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self.api_key_header = api_key_header
        self.api_key = api_key
        self._certfile = certfile
        self._keyfile = keyfile
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/", "/data"}
        self.sessions: set[str] = set()
        # (method, path, lower-cased headers, command or None), in arrival order
        self.requests: list[tuple[str, str, dict[str, str], str | None]] = []
        self.redirect_all_to: str | None = None
        self.corrupt_md5 = False
        # Folder a listing of "/" answers with, as the real server does for an
        # account that has a single virtual folder.
        self.single_root: str | None = None
        self.base_url = ""
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> "FakeCrushFtp":
        fake = self

        class Handler(_Handler):
            server_state = fake

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # A client that rejects the certificate drops the connection mid-
        # handshake; that is the test's point, not something to print.
        self._server.handle_error = lambda request, client_address: None
        scheme = "http"
        if self._certfile:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self._certfile, self._keyfile)
            self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
            scheme = "https"
        self.base_url = f"{scheme}://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "FakeCrushFtp":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # -- helpers for tests --------------------------------------------------
    def commands(self) -> list[str]:
        return [command for _, _, _, command in self.requests if command]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_state: FakeCrushFtp

    def log_message(self, *args: object) -> None:  # keep test output quiet
        pass

    # -- plumbing -----------------------------------------------------------
    def _send(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None, *, head: bool = False) -> None:
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if "Content-Length" not in (headers or {}):
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            data = b""
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    self.rfile.readline()
                    return data
                data += self.rfile.read(size)
                self.rfile.readline()
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _session(self) -> str | None:
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "CrushAuth" and value in self.server_state.sessions:
                return value
        return None

    def _key_ok(self) -> bool:
        state = self.server_state
        if state.api_key_header is None:
            return True
        return self.headers.get(state.api_key_header) == state.api_key

    def _authenticated(self) -> bool:
        state = self.server_state
        if not self._key_ok():
            return False
        return self._session() is not None or state.api_key_header is not None

    def _record(self, command: str | None = None) -> None:
        headers = {name.lower(): value for name, value in self.headers.items()}
        self.server_state.requests.append((self.command, unquote(urlsplit(self.path).path), headers, command))

    def _preflight(self) -> bool:
        """For GET/HEAD: True when the request was already answered (redirect
        / bad key)."""
        state = self.server_state
        if state.redirect_all_to:
            self._send(302, headers={"Location": state.redirect_all_to}, head=self.command == "HEAD")
            return True
        if not self._key_ok():
            self._send(401, b"Unauthorized", head=self.command == "HEAD")
            return True
        return False

    # -- commands -----------------------------------------------------------
    def do_POST(self) -> None:
        state = self.server_state
        raw = self._read_body().decode()
        # The real server does not turn "+" into a space, so neither does this.
        fields = dict(parse_qsl(raw.replace("+", "%2B"), keep_blank_values=True))
        command = fields.get("command")
        self._record(command)
        if state.redirect_all_to:
            self._send(302, headers={"Location": state.redirect_all_to})
            return
        if not self._key_ok():
            self._send(401, b"Unauthorized")
            return
        if urlsplit(self.path).path != _FUNCTION_PATH:
            self._send(404, b"The selected resource was not found.\n")
            return
        if command == "login":
            self._login(fields)
            return
        if not self._authenticated():
            self._send(404, b"The selected resource was not found.\n")
            return
        session = self._session()
        if command in _MUTATING and session is not None and fields.get("c2f") != session[-4:]:
            self._xml("commandResult", "FAILURE:Access Denied. (c2f)", encode=False)
            return
        if command == "getXMLListing":
            self._listing(fields.get("path", ""))
        elif command == "makedir":
            self._makedir(fields.get("path", ""))
        elif command == "rename":
            self._rename(fields.get("path", ""), fields.get("name1", ""), fields.get("name2", ""))
        elif command == "delete":
            self._delete(fields.get("names", ""))
        elif command == "logout":
            state.sessions.discard(session or "")
            self._xml("commandResult", "Logged out.", encode=False)
        else:
            self._xml("commandResult", f"unknown command {command}")

    def _xml(self, root: str, text: str, *, encode: bool = True, extra: str = "", headers: dict[str, str] | None = None) -> None:
        payload = quote(text, safe="") if encode else text
        body = f'<?xml version="1.0" encoding="UTF-8"?> \n<{root}><response>{payload}</response>{extra}</{root}>\n'
        self._send(200, body.encode(), {"Content-Type": "text/xml;charset=utf-8", **(headers or {})})

    def _login(self, fields: dict[str, str]) -> None:
        state = self.server_state
        if fields.get("username") != state.username or fields.get("password") != state.password:
            self._xml(
                "loginResult", "failure", encode=False,
                extra="<message>Check your username or password and try again.\n530%20Access%20denied.%3A</message>",
            )
            return
        token = f"1791589664447_{secrets.token_urlsafe(24)}"
        state.sessions.add(token)
        self.send_response(200)
        body = (
            '<?xml version="1.0" encoding="UTF-8"?> \n'
            f"<loginResult><response>success</response><c2f>{token[-4:]}</c2f></loginResult>\n"
        ).encode()
        self.send_header("Content-Type", "text/xml;charset=utf-8")
        self.send_header("Set-Cookie", f"currentAuth={token[-4:]}; path=/")
        self.send_header("Set-Cookie", f"CrushAuth={token}; path=/; HttpOnly")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _listing(self, path: str) -> None:
        state = self.server_state
        directory = path.rstrip("/") or "/"
        if directory == "/" and state.single_root:
            directory = state.single_root
        if not path.endswith("/") or directory not in state.dirs:
            self._send(200, b'{ "error_msg" : "error_item" }\n', {"Content-Type": "application/jsonrequest;charset=utf-8"})
            return
        prefix = "/" if directory == "/" else directory + "/"
        listing = []
        for file_path in sorted(state.files):
            if posixpath.dirname(file_path) == directory:
                listing.append({
                    "name": posixpath.basename(file_path).replace("%", "%25"), "type": "FILE",
                    "size": str(len(state.files[file_path])), "modified": "1791589698000",
                })
        for dir_path in sorted(state.dirs):
            if dir_path != "/" and posixpath.dirname(dir_path) == directory:
                listing.append({
                    "name": posixpath.basename(dir_path).replace("%", "%25"), "type": "DIR",
                    "size": "1", "modified": "1791589698000",
                })
        body = json.dumps({"path": prefix, "listing": listing}).encode()
        self._send(200, body, {"Content-Type": "application/jsonrequest;charset=utf-8"})

    def _makedir(self, path: str) -> None:
        current = ""
        for part in [p for p in path.split("/") if p]:
            current = f"{current}/{part}"
            self.server_state.dirs.add(current)
        self._xml("commandResult", "")

    def _rename(self, path: str, name1: str, name2: str) -> None:
        state = self.server_state
        source = posixpath.normpath(posixpath.join(path, name1))
        target = posixpath.normpath(posixpath.join(path, name2))
        if source not in state.files:
            self._xml("commandResult", "%RNFR-bad%ERROR:Access not allowed.\r\n")
            return
        if target in state.files or target in state.dirs or posixpath.dirname(target) not in state.dirs:
            self._xml("commandResult", "File rename error. (bad path?  in use?  locked?)")
            return
        state.files[target] = state.files.pop(source)
        self._xml("commandResult", "")

    def _delete(self, names: str) -> None:
        state = self.server_state
        for name in [n for n in names.split("\r\n") if n]:
            target = name.rstrip("/") or "/"
            state.files.pop(target, None)
            if target in state.dirs and target != "/":
                state.dirs = {d for d in state.dirs if d != target and not d.startswith(target + "/")}
                state.files = {f: b for f, b in state.files.items() if not f.startswith(target + "/")}
        self._xml("commandResult", "")

    # -- files --------------------------------------------------------------
    def _file_request(self, *, head: bool) -> None:
        state = self.server_state
        self._record()
        if self._preflight():
            return
        path = unquote(urlsplit(self.path).path)
        if not self._authenticated():
            self._send(302, headers={"Location": f"/WebInterface/login.html?path={path}"}, head=head)
            return
        if path in state.dirs:
            self._send(200, b"\n", {"Content-Type": "application/binary"}, head=head)
            return
        if path not in state.files:
            self._send(404, b"The selected resource was not found.\n", head=head)
            return
        data = state.files[path]
        headers = {"Content-Type": "application/binary", "Accept-Ranges": "bytes"}
        byte_range = self.headers.get("Range")
        if byte_range and data:
            start_text, _, end_text = byte_range.removeprefix("bytes=").partition("-")
            start = int(start_text or 0)
            end = min(int(end_text) if end_text else len(data) - 1, len(data) - 1)
            chunk = data[start:end + 1]
            headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
            headers["Content-Length"] = str(len(chunk))
            self._send(206, chunk, headers, head=head)
            return
        headers["Content-Length"] = str(len(data))
        self._send(200, data, headers, head=head)

    def do_GET(self) -> None:
        self._file_request(head=False)

    def do_HEAD(self) -> None:
        self._file_request(head=True)

    def do_PUT(self) -> None:
        state = self.server_state
        self._record()
        body = self._read_body()
        if state.redirect_all_to:
            self._send(302, headers={"Location": state.redirect_all_to})
            return
        if not self._authenticated():
            self._send(401, b"Unauthorized", {"WWW-Authenticate": 'Basic realm="fake"'})
            return
        path = unquote(urlsplit(self.path).path)
        if posixpath.dirname(path) not in state.dirs or posixpath.dirname(path) == "/":
            self._send(403, b"\r\n")
            return
        state.files[path] = body
        digest = "0" * 32 if state.corrupt_md5 else hashlib.md5(body).hexdigest()
        self._send(201, (digest + "\r\n").encode())
