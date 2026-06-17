import imaplib
import email
import os
from email.header import decode_header as _decode_header


def _decode(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    parts = _decode_header(value)
    out = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(chunk)
    return " ".join(out)


class IMAPClient:
    def __init__(self):
        self.host = os.getenv("IMAP_HOST")
        self.port = int(os.getenv("IMAP_PORT", 993))
        self.user = os.getenv("IMAP_USER")
        self.password = os.getenv("IMAP_PASSWORD")
        self.use_ssl = os.getenv("IMAP_USE_SSL", "true").lower() == "true"

    def _connect(self):
        if self.use_ssl:
            return imaplib.IMAP4_SSL(self.host, self.port)
        return imaplib.IMAP4(self.host, self.port)

    def fetch_emails(self, folder: str, limit: int = None, unseen_only: bool = False) -> list[dict]:
        conn = self._connect()
        try:
            conn.login(self.user, self.password)
            status, _ = conn.select(folder, readonly=True)
            if status != "OK":
                return []

            criteria = "UNSEEN" if unseen_only else "ALL"
            _, data = conn.search(None, criteria)
            ids = data[0].split()

            if not ids:
                return []

            if limit:
                ids = ids[-limit:]

            results = []
            for msg_id in reversed(ids):
                _, raw_data = conn.fetch(msg_id, "(RFC822)")
                if not raw_data or raw_data[0] is None:
                    continue
                raw = raw_data[0][1]
                msg = email.message_from_bytes(raw)

                message_id = msg.get("Message-ID", "").strip()
                if not message_id:
                    continue

                results.append({
                    "message_id": message_id,
                    "folder": folder,
                    "sender": _decode(msg.get("From", "")),
                    "subject": _decode(msg.get("Subject", "")),
                    "headers": {k.lower(): v for k, v in msg.items()},
                    "msg": msg,
                })
            return results
        finally:
            try:
                conn.logout()
            except Exception:
                pass
