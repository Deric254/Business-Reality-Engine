"""
Shared-folder connector (Section 8-G): legacy environments that drop exports into a folder.

The operator mounts or syncs each business's share under CONNECTOR_FILE_ROOT/<tenant_id>/. A connector can only read
below that directory (app/core/netguard.tenant_path), so tenants are isolated and a hostile path cannot escape.
Files go through the same parsing/mapping/validation as uploads. Processed files are tracked by CONTENT HASH (not just
name+mtime): a re-copied identical file is skipped, a corrected file with new content is picked up.
"""
import fnmatch
import hashlib
from pathlib import Path

from fastapi import HTTPException

from app.connectors.file_connector import ALLOWED_EXTENSIONS, MAX_FILE_SIZE_BYTES
from app.core.netguard import tenant_path

MAX_FILES_PER_SYNC = 200
MAX_TRACKED = 5000


def list_new_files(tenant_id: str, config: dict) -> list[dict]:
    folder = tenant_path(tenant_id, config.get("path", ""))
    if not folder.is_dir():
        raise HTTPException(400, "The configured path is not a folder")
    pattern = config.get("file_pattern", "*")
    seen = {h for h in config.get("processed_hashes", [])}
    base = folder
    out = []
    for p in sorted(folder.iterdir()):
        if len(out) >= MAX_FILES_PER_SYNC:
            break
        if not p.is_file() or p.is_symlink() or not fnmatch.fnmatch(p.name, pattern):
            continue
        if p.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        resolved = p.resolve()
        if base.resolve() not in resolved.parents:
            continue
        size = p.stat().st_size
        if size > MAX_FILE_SIZE_BYTES or size == 0:
            out.append({"filename": p.name, "path": resolved, "hash": None, "size": size, "skip": "empty" if size == 0 else "too large"})
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        if digest in seen:
            continue
        out.append({"filename": p.name, "path": resolved, "hash": digest, "size": size, "skip": None})
    return out


def read_file(item: dict) -> bytes:
    return Path(item["path"]).read_bytes()
