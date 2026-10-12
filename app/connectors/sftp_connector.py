"""
SFTP connector (Section 8-F): scheduled file exchange.

Reuses file_connector.py's parsing (CSV/XLSX -> events) entirely — the
only new part is FETCHING the file over SFTP instead of receiving it via
HTTP upload. Same ColumnMapping, same size/row caps, same extension
allowlist; a file arriving via SFTP is not more trustworthy than one
uploaded through the browser.

Section 11 (incremental updates): a business's SFTP directory typically
accumulates files over time (a new export dropped daily). Re-syncing
must not re-ingest files already processed — tracked in
Connector.config["processed_files"] as filename+mtime pairs.
"""
import io
from typing import Optional
from fastapi import HTTPException
from pydantic import BaseModel
import paramiko

from app.core.netguard import check_host_allowed
from app.core.secrets import decrypt_config_secrets
from app.connectors.file_connector import (
    ColumnMapping, validate_upload, parse_file_to_dataframe, dataframe_to_normalized_events,
    MAX_FILE_SIZE_BYTES,
)


class SftpConnectionConfig(BaseModel):
    host: str
    port: int = 22
    username: str
    password: str            # encrypted at rest, same as DatabaseConnectionConfig.password
    remote_directory: str
    file_pattern: str = "*"   # simple glob — only * and ? wildcards, matched via fnmatch
    host_key_sha256: Optional[str] = None   # base64 SHA-256 of the server's host key; pin it to rule out a man-in-the-middle


def _sftp_connect(config: SftpConnectionConfig) -> tuple[paramiko.Transport, paramiko.SFTPClient]:
    check_host_allowed(config.host)
    try:
        transport = paramiko.Transport((config.host, config.port))
        transport.connect(username=config.username, password=config.password)
        if config.host_key_sha256:
            import base64, hashlib
            key = transport.get_remote_server_key()
            seen = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
            if seen != config.host_key_sha256.rstrip("=").replace("SHA256:", ""):
                transport.close()
                raise HTTPException(502, "SFTP host key does not match the pinned fingerprint; refusing to send credentials")
        sftp = paramiko.SFTPClient.from_transport(transport)
        return transport, sftp
    except HTTPException:
        raise
    except Exception as e:
        # Same principle as every other error handler here — never leak
        # host/credential details in the client-facing error.
        raise HTTPException(502, f"Could not connect to SFTP server: {type(e).__name__}")


def list_new_files(connector_config: dict) -> list[dict]:
    """
    Returns files in the configured remote directory matching the pattern,
    EXCLUDING ones already recorded as processed (same name + same mtime —
    if a file is overwritten with new content at the same name, its mtime
    changes and it's treated as new; Section 11's 'incremental updates').
    """
    import fnmatch

    decrypted = decrypt_config_secrets(connector_config)
    try:
        conn_config = SftpConnectionConfig(**{k: v for k, v in decrypted.items() if k in SftpConnectionConfig.model_fields})
    except Exception as e:
        raise HTTPException(400, f"SFTP connector config is invalid: {e}")

    processed = {tuple(p) for p in connector_config.get("processed_files", [])}

    transport, sftp = _sftp_connect(conn_config)
    try:
        try:
            entries = sftp.listdir_attr(conn_config.remote_directory)
        except FileNotFoundError:
            raise HTTPException(400, f"Remote directory not found: {conn_config.remote_directory}")

        new_files = []
        for entry in entries:
            if not fnmatch.fnmatch(entry.filename, conn_config.file_pattern):
                continue
            key = (entry.filename, entry.st_mtime)
            if key in processed:
                continue
            new_files.append({"filename": entry.filename, "mtime": entry.st_mtime, "size": entry.st_size})
        return new_files
    finally:
        sftp.close()
        transport.close()


def fetch_file(connector_config: dict, filename: str) -> bytes:
    decrypted = decrypt_config_secrets(connector_config)
    conn_config = SftpConnectionConfig(**{k: v for k, v in decrypted.items() if k in SftpConnectionConfig.model_fields})

    transport, sftp = _sftp_connect(conn_config)
    try:
        remote_path = f"{conn_config.remote_directory.rstrip('/')}/{filename}"
        # Enforce the same size cap as browser uploads BEFORE downloading
        # the full content — a malicious/corrupt file on the remote side
        # shouldn't be able to exhaust memory just because it arrived via
        # a trusted-looking channel.
        stat = sftp.stat(remote_path)
        if stat.st_size > MAX_FILE_SIZE_BYTES:
            raise HTTPException(413, f"Remote file '{filename}' exceeds {MAX_FILE_SIZE_BYTES // (1024*1024)}MB limit")

        buf = io.BytesIO()
        sftp.getfo(remote_path, buf)
        return buf.getvalue()
    finally:
        sftp.close()
        transport.close()
