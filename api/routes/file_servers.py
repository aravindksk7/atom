from __future__ import annotations

import hashlib
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import FileServerProfileCreate, FileServerProfileOut, FileServerProfileUpdate, FileServerTestResult
from api.services.audit_service import AuditService
from etl_framework.repository.repository import FileServerProfileRepository, _FILE_SERVER_SECRET_FIELDS
from etl_framework.repository.models import ExecutionSequenceVersion, SavedJob, ScheduledRun

try:
    import paramiko
except ImportError:
    # Every other paramiko/boto3 user in this codebase (multi_file_remote.py)
    # imports lazily inside the function that needs it, so the app still starts
    # fine without paramiko installed -- SFTP/SCP is one optional feature among
    # several. `paramiko = None` (rather than skipping the name entirely) keeps
    # this a stable, patchable module attribute for tests, and lets
    # test_file_server below give a clean FileServerTestResult(status="error")
    # instead of an unhandled crash when an sftp/scp connection is attempted.
    paramiko = None

router = APIRouter(tags=["file-servers"])

_MASK = "********"
_SECRET_FIELDS = _FILE_SERVER_SECRET_FIELDS  # single source of truth, defined alongside FileServerProfileRepository


def _mask(profile) -> FileServerProfileOut:
    out = FileServerProfileOut.model_validate(profile)
    data = out.model_dump()
    for field in _SECRET_FIELDS:
        if data.get(field):
            data[field] = _MASK
    return FileServerProfileOut(**data)


def _preserve_masked_secrets(incoming: dict, existing) -> dict:
    """Drop a secret field from the update payload when the client echoes back
    the display mask, so the stored (already-encrypted) value is left alone.
    Setting it to `getattr(existing, field)` instead would re-run it through
    FileServerProfileRepository's `_encrypt_fields`, encrypting an
    already-encrypted ciphertext and permanently losing the original secret
    on the next decrypt."""
    if existing is None:
        return incoming
    result = dict(incoming)
    for field in _SECRET_FIELDS:
        if result.get(field) == _MASK:
            del result[field]
    return result


def _references_credentials_ref(value, name: str) -> bool:
    """Recursively scan a saved job's params / a sequence step / a schedule's
    job_sequence for a `credentials_ref` key equal to `name`. Generic rather
    than shape-specific because the three JSON columns that can carry a
    credentials_ref (SavedJob.params, ExecutionSequenceVersion.steps_json,
    ScheduledRun.job_sequence) nest it at different depths."""
    if isinstance(value, dict):
        if value.get("credentials_ref") == name:
            return True
        return any(_references_credentials_ref(v, name) for v in value.values())
    if isinstance(value, list):
        return any(_references_credentials_ref(v, name) for v in value)
    return False


@router.get("", response_model=list[FileServerProfileOut])
def list_file_servers(db: Session = Depends(get_session)):
    return [_mask(p) for p in FileServerProfileRepository(db).list()]


@router.post("", response_model=FileServerProfileOut, status_code=201)
def create_file_server(body: FileServerProfileCreate, request: Request, db: Session = Depends(get_session)):
    repo = FileServerProfileRepository(db)
    if repo.get_by_name(body.name) is not None:
        raise HTTPException(status_code=409, detail=f"File server profile '{body.name}' already exists")
    profile = repo.create(body.model_dump())
    AuditService(db).log(request, "file_server.created", "file_server", profile.id, {"name": profile.name, "kind": profile.kind})
    return _mask(profile)


@router.get("/{profile_id}", response_model=FileServerProfileOut)
def get_file_server(profile_id: int, db: Session = Depends(get_session)):
    profile = FileServerProfileRepository(db).get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="File server profile not found")
    return _mask(profile)


@router.put("/{profile_id}", response_model=FileServerProfileOut)
def update_file_server(profile_id: int, body: FileServerProfileUpdate, request: Request, db: Session = Depends(get_session)):
    repo = FileServerProfileRepository(db)
    existing = repo.get(profile_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="File server profile not found")
    data = _preserve_masked_secrets({k: v for k, v in body.model_dump().items() if v is not None}, existing)
    profile = repo.update(profile_id, data)
    AuditService(db).log(request, "file_server.updated", "file_server", profile.id, {"name": profile.name})
    return _mask(profile)


@router.delete("/{profile_id}", status_code=204)
def delete_file_server(profile_id: int, request: Request, db: Session = Depends(get_session)):
    repo = FileServerProfileRepository(db)
    profile = repo.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="File server profile not found")

    in_use = (
        any(_references_credentials_ref(j.params, profile.name) for j in db.query(SavedJob).all())
        or any(_references_credentials_ref(v.steps_json, profile.name) for v in db.query(ExecutionSequenceVersion).all())
        or any(_references_credentials_ref(s.job_sequence, profile.name) for s in db.query(ScheduledRun).all())
    )
    if in_use:
        raise HTTPException(status_code=409, detail=f"File server profile '{profile.name}' is still referenced by a saved job, sequence, or schedule")

    repo.delete(profile_id)
    AuditService(db).log(request, "file_server.deleted", "file_server", profile_id, {"name": profile.name})


class TestConnectionRequest(BaseModel):
    accept_fingerprint: bool = False


@router.post("/{profile_id}/test", response_model=FileServerTestResult)
def test_file_server(profile_id: int, body: TestConnectionRequest = TestConnectionRequest(), db: Session = Depends(get_session)):
    repo = FileServerProfileRepository(db)
    existing = repo.get(profile_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="File server profile not found")
    profile = repo.get_decrypted_by_name(existing.name)

    if profile.kind in ("sftp", "scp"):
        if paramiko is None:
            return FileServerTestResult(status="error", message="paramiko is not installed -- SFTP/SCP connections are unavailable")
        # Reuse the same working key-loading helper build_sftp_client relies
        # on, instead of calling paramiko.PKey.from_private_key() directly.
        # That classmethod only works when called on a concrete subclass
        # (RSAKey/Ed25519Key/ECDSAKey), not the abstract PKey base -- calling
        # it directly raises TypeError under the paramiko version this
        # project pins (see _load_sftp_private_key's docstring in
        # api/services/multi_file_remote.py for the full explanation).
        from api.services.multi_file_remote import _load_sftp_private_key

        transport = None
        try:
            # paramiko.Transport.__init__ resolves the host (socket.getaddrinfo)
            # synchronously, so an unreachable/unresolvable host raises right here --
            # it must stay inside this try, not before it, or a bad host 500s instead
            # of surfacing as a normal FileServerTestResult(status="error").
            transport = paramiko.Transport((profile.host, int(profile.port or 22)))
            if profile.auth_method == "private_key":
                key = _load_sftp_private_key(profile.private_key, profile.key_passphrase or None)
                transport.connect(username=profile.username, pkey=key)
            else:
                transport.connect(username=profile.username, password=profile.password)
            presented = transport.get_remote_server_key()
            fingerprint = hashlib.sha256(presented.asbytes()).hexdigest()
        except Exception as exc:
            return FileServerTestResult(status="error", message=str(exc))
        finally:
            if transport is not None:
                transport.close()

        if body.accept_fingerprint:
            repo.update(profile_id, {"host_key_fingerprint": fingerprint})
            return FileServerTestResult(status="ok", presented_fingerprint=fingerprint)
        if not profile.host_key_fingerprint:
            return FileServerTestResult(status="unpinned", presented_fingerprint=fingerprint)
        if fingerprint != profile.host_key_fingerprint:
            return FileServerTestResult(status="mismatch", presented_fingerprint=fingerprint, pinned_fingerprint=profile.host_key_fingerprint)
        return FileServerTestResult(status="ok", presented_fingerprint=fingerprint)

    if profile.kind == "smb":
        if os.name != "nt":
            return FileServerTestResult(status="error", message="SMB transfers require the atom server to run on Windows")
        if not profile.host:
            return FileServerTestResult(status="error", message="SMB profile requires a host")
        # _net_use/_net_use_delete/_smb_host_lock are module-private helpers
        # shared between this route and multi_file_remote's real connect path
        # (same convention as _load_sftp_private_key above).
        from api.services.multi_file_remote import SmbConnectError, _net_use, _net_use_delete, _smb_host_lock, valid_smb_host

        if not valid_smb_host(profile.host):
            return FileServerTestResult(status="error", message="SMB host must be a server name or IP address")
        resource = f"\\\\{profile.host.strip()}\\IPC$"
        lock = _smb_host_lock(profile.host.strip())
        if not lock.acquire(timeout=30):
            return FileServerTestResult(status="error", message="Another SMB operation is in progress for this host -- try again shortly")
        try:
            try:
                _net_use(resource, profile.username, profile.password)
            except SmbConnectError as exc:
                return FileServerTestResult(status="error", message=str(exc))
            except Exception:
                return FileServerTestResult(status="error", message="SMB connection failed -- see server logs")
            finally:
                try:
                    _net_use_delete(resource)
                except Exception:
                    pass
        finally:
            lock.release()
        return FileServerTestResult(status="ok")

    # s3
    try:
        from api.services.multi_file_remote import build_s3_client
        from etl_framework.reconciliation.file_mapping import FileSourceSpec
        client = build_s3_client(profile, FileSourceSpec(kind="s3", root="", pattern=""))
        client.list_buckets()
        return FileServerTestResult(status="ok")
    except Exception as exc:
        return FileServerTestResult(status="error", message=str(exc))
