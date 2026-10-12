"""
Outbound-connection and filesystem guards for connectors (Section 9 / SECURITY.md).

  check_host_allowed   refuse private/loopback/link-local targets when BLOCK_PRIVATE_CONNECTOR_HOSTS=true (set it on any
                       multi-tenant cloud deployment so a connector cannot be used to probe the operator's own network).
                       Checked at configuration time AND at connect time (DNS can change in between).
  tenant_path          shared-folder and SQLite-file connectors may only read inside CONNECTOR_FILE_ROOT/<tenant_id>/,
                       so one business can never read another's files; symlinks that leave that directory are refused.
"""
import ipaddress
import os
import socket
from pathlib import Path

from fastapi import HTTPException


def _block_private() -> bool:
    return os.environ.get("BLOCK_PRIVATE_CONNECTOR_HOSTS", "").lower() == "true"


def check_host_allowed(host: str) -> None:
    if not host or not isinstance(host, str) or len(host) > 255:
        raise HTTPException(400, "A valid host is required")
    if not _block_private():
        return
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise HTTPException(400, "Host does not resolve")
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise HTTPException(400, "That host resolves to a private or internal address, which is not allowed here. "
                                     "Use the local agent for systems inside a private network.")


def file_root() -> Path:
    root = os.environ.get("CONNECTOR_FILE_ROOT", "").strip()
    if not root:
        raise HTTPException(501, "File-based connectors are not enabled on this server (CONNECTOR_FILE_ROOT is not set)")
    return Path(root).resolve()


def tenant_path(tenant_id: str, relative: str, *, must_exist: bool = True) -> Path:
    """Resolve `relative` strictly inside <root>/<tenant_id>/ . Absolute paths, '..' and escaping symlinks are refused."""
    if not relative or not isinstance(relative, str) or "\x00" in relative:
        raise HTTPException(400, "A path is required")
    base = (file_root() / tenant_id).resolve()
    if Path(relative).is_absolute() or relative.startswith(("~", "\\")):
        raise HTTPException(400, "Use a path relative to your business folder, not an absolute path")
    target = (base / relative).resolve()
    if target != base and base not in target.parents:
        raise HTTPException(400, "That path is outside your business folder")
    if must_exist and not target.exists():
        raise HTTPException(400, "Path not found in your business folder")
    return target
