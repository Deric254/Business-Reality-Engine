"""
Email connector (Section 8-H): where systems automatically send
structured reports.

Same pattern as sftp_connector.py — this only handles FETCHING (via IMAP,
searching for unprocessed messages, extracting attachments); parsing goes
through file_connector.py's existing CSV/XLSX logic unchanged. Processed
messages are tracked by a stable identifier (Message-ID, not IMAP UID —
UIDs aren't guaranteed stable across IMAP sessions per the protocol spec,
Message-ID is what's actually safe to key dedup on).
"""
import imaplib
import email
from email.message import Message
from typing import Optional
from fastapi import HTTPException
from pydantic import BaseModel

from app.core.netguard import check_host_allowed
from app.core.secrets import decrypt_config_secrets
from app.connectors.file_connector import ALLOWED_EXTENSIONS


class EmailConnectionConfig(BaseModel):
    imap_host: str
    imap_port: int = 993
    use_ssl: bool = True    # encrypted by default; set false only for a trusted local test server
    username: str
    password: str            # encrypted at rest
    mailbox: str = "INBOX"
    from_filter: Optional[str] = None       # substring match against the From header, if set
    subject_filter: Optional[str] = None     # substring match against Subject, if set


def _imap_connect(config: EmailConnectionConfig) -> imaplib.IMAP4:
    check_host_allowed(config.imap_host)
    try:
        if config.use_ssl:
            conn = imaplib.IMAP4_SSL(config.imap_host, config.imap_port)
        else:
            conn = imaplib.IMAP4(config.imap_host, config.imap_port)
        conn.login(config.username, config.password)
        conn.select(config.mailbox)
        return conn
    except Exception as e:
        raise HTTPException(502, f"Could not connect to email server: {type(e).__name__}")


def _extract_attachments(msg: Message) -> list[tuple[str, bytes]]:
    """Returns (filename, content) for every attachment with an allowed extension."""
    attachments = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            attachments.append((filename, payload))
    return attachments


def list_new_emails(connector_config: dict) -> list[dict]:
    """
    Returns unprocessed messages matching the connector's filters, each
    with its attachments already extracted. Dedup key is Message-ID, not
    IMAP UID (see module docstring).
    """
    decrypted = decrypt_config_secrets(connector_config)
    try:
        conn_config = EmailConnectionConfig(**{k: v for k, v in decrypted.items() if k in EmailConnectionConfig.model_fields})
    except Exception as e:
        raise HTTPException(400, f"Email connector config is invalid: {e}")

    processed_ids = set(connector_config.get("processed_message_ids", []))

    conn = _imap_connect(conn_config)
    try:
        typ, data = conn.search(None, "ALL")
        if typ != "OK":
            raise HTTPException(502, "Could not search mailbox")

        results = []
        for num in data[0].split():
            typ, msg_data = conn.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            message_id = msg.get("Message-ID", f"no-id-{num.decode()}")
            if message_id in processed_ids:
                continue

            from_header = msg.get("From", "")
            subject = msg.get("Subject", "")
            if conn_config.from_filter and conn_config.from_filter not in from_header:
                continue
            if conn_config.subject_filter and conn_config.subject_filter not in subject:
                continue

            attachments = _extract_attachments(msg)
            if not attachments:
                continue  # a matching email with no usable attachment has nothing to ingest

            results.append({"message_id": message_id, "from": from_header, "subject": subject, "attachments": attachments})
        return results
    finally:
        conn.logout()
