import logging
import os
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from .classifier import classify_urls
from .database import get_conn, is_initial_run
from .extractor import extract_candidate_urls, get_html_body, is_likely_tracker
from .imap_client import IMAPClient
from .prescreener import is_commercial

logger = logging.getLogger(__name__)

FOLDERS = [f.strip() for f in os.getenv("IMAP_FOLDERS", "INBOX,Junk").split(",")]
INITIAL_LIMIT = int(os.getenv("IMAP_INITIAL_LIMIT", "50"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_MINUTES", "30"))


def scan():
    initial = is_initial_run()
    client = IMAPClient()

    for folder in FOLDERS:
        try:
            emails = client.fetch_emails(
                folder,
                limit=INITIAL_LIMIT if initial else None,
                unseen_only=not initial,
            )
            checked = 0
            new_candidates = 0

            for item in emails:
                with get_conn() as conn:
                    already = conn.execute(
                        "SELECT 1 FROM processed_emails WHERE message_id = ?",
                        (item["message_id"],),
                    ).fetchone()

                if already:
                    continue

                checked += 1

                if not is_commercial(item["headers"]):
                    _mark_processed(item["message_id"], folder)
                    continue

                html = get_html_body(item["msg"])
                candidates = extract_candidate_urls(html)

                # prefer URLs that already look like trackers; fall back to all candidates
                to_classify = [u for u in candidates if is_likely_tracker(u)] or candidates[:10]

                if to_classify:
                    results = classify_urls(to_classify)
                    for r in results:
                        if r.get("is_tracker") and r.get("confidence") in ("high", "medium"):
                            _store_candidate(r, item)
                            new_candidates += 1

                _mark_processed(item["message_id"], folder)

            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO scan_log (scanned_at, folder, emails_checked, new_candidates) VALUES (?, ?, ?, ?)",
                    (datetime.now(timezone.utc).isoformat(), folder, checked, new_candidates),
                )

            logger.info("[%s] checked=%d new_candidates=%d", folder, checked, new_candidates)

        except Exception:
            logger.exception("Error scanning folder %s", folder)


def _mark_processed(message_id: str, folder: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_emails (message_id, folder, processed_at) VALUES (?, ?, ?)",
            (message_id, folder, datetime.now(timezone.utc).isoformat()),
        )


def _store_candidate(result: dict, item: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO tracker_candidates
               (domain, url_example, source_message_id, source_sender, source_subject,
                found_at, claude_reasoning, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                result["domain"],
                result["url"],
                item["message_id"],
                item["sender"],
                item["subject"],
                datetime.now(timezone.utc).isoformat(),
                result.get("reason", ""),
            ),
        )


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        scan,
        "interval",
        minutes=POLL_INTERVAL,
        id="imap_scan",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=2),
    )
    scheduler.start()
    return scheduler
