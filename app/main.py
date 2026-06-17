import asyncio
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import get_conn, init_db
from .extractor import _is_esp_click_domain, _NON_IMAGE_EXT_RE
from .scheduler import scan, start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

APP_VERSION = "1.0.0"
GITHUB_URL = "https://github.com/Marvellous-Codeworks/mailtrack-hunter"
BLOCK_MAILTRACK_GITHUB = "https://github.com/Marvellous-Codeworks/block-mailtrack"
_BLOCK_MAILTRACK_RULES_URL = "https://raw.githubusercontent.com/Marvellous-Codeworks/block-mailtrack/master/rules/rules.json"
_RULES_CACHE: list | None = None
_RULES_CACHE_AT: float = 0
_RULES_CACHE_TTL = 3600

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    init_db()
    _scheduler = start_scheduler()
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="Mailtrack Hunter", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    already_blocked = _already_blocked_domains()
    with get_conn() as conn:
        stats = {
            "pending":   conn.execute("SELECT COUNT(*) FROM tracker_candidates WHERE status='pending'").fetchone()[0],
            "approved":  conn.execute("SELECT COUNT(*) FROM tracker_candidates WHERE status='approved'").fetchone()[0],
            "rejected":  conn.execute("SELECT COUNT(*) FROM tracker_candidates WHERE status='rejected'").fetchone()[0],
            "processed": conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0],
        }
        last_scan_row = conn.execute(
            "SELECT scanned_at, folder, emails_checked, new_candidates FROM scan_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        candidates = [dict(r) for r in conn.execute(
            "SELECT * FROM tracker_candidates ORDER BY found_at DESC"
        ).fetchall()]
        unproposed_count = conn.execute(
            "SELECT COUNT(*) FROM tracker_candidates WHERE status='approved' AND github_proposed_at IS NULL"
        ).fetchone()[0]

    # subtract those already covered by block-mailtrack rules
    if already_blocked:
        unproposed_count = sum(
            1 for c in candidates
            if c["status"] == "approved"
            and c["github_proposed_at"] is None
            and c["domain"] not in already_blocked
        )

    last_scan = dict(last_scan_row) if last_scan_row else None

    return templates.TemplateResponse("index.html", {
        "request":         request,
        "stats":           stats,
        "last_scan":       last_scan,
        "candidates":      candidates,
        "poll_interval":   int(os.getenv("POLL_INTERVAL_MINUTES", 30)),
        "version":         APP_VERSION,
        "github_url":      GITHUB_URL,
        "unproposed_count": unproposed_count,
        "rules_cache":     _rules_cache_info(),
    })


@app.post("/api/candidates/{candidate_id}/approve")
async def approve(candidate_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE tracker_candidates SET status='approved' WHERE id=?", (candidate_id,))
    return {"ok": True}


@app.post("/api/candidates/{candidate_id}/reject")
async def reject(candidate_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE tracker_candidates SET status='rejected' WHERE id=?", (candidate_id,))
    return {"ok": True}


@app.post("/api/candidates/{candidate_id}/reset")
async def reset_status(candidate_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE tracker_candidates SET status='pending' WHERE id=?", (candidate_id,))
    return {"ok": True}


@app.post("/api/candidates/bulk-approve")
async def bulk_approve(ids: list[int]):
    with get_conn() as conn:
        conn.executemany(
            "UPDATE tracker_candidates SET status='approved' WHERE id=?",
            [(i,) for i in ids]
        )
    return {"ok": True, "updated": len(ids)}


@app.post("/api/candidates/bulk-reject")
async def bulk_reject(ids: list[int]):
    with get_conn() as conn:
        conn.executemany(
            "UPDATE tracker_candidates SET status='rejected' WHERE id=?",
            [(i,) for i in ids]
        )
    return {"ok": True, "updated": len(ids)}


@app.post("/api/candidates/bulk-delete")
async def bulk_delete(ids: list[int]):
    with get_conn() as conn:
        conn.executemany(
            "DELETE FROM tracker_candidates WHERE id=?",
            [(i,) for i in ids]
        )
    return {"ok": True, "deleted": len(ids)}


@app.delete("/api/candidates/{candidate_id}")
async def delete_candidate(candidate_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM tracker_candidates WHERE id=?", (candidate_id,))
    return {"ok": True}


@app.post("/api/candidates/purge-rejected")
async def purge_rejected():
    with get_conn() as conn:
        result = conn.execute("DELETE FROM tracker_candidates WHERE status='rejected'")
    return {"ok": True, "deleted": result.rowcount}


def _parent(domain: str) -> str:
    parts = domain.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


@app.api_route("/api/auto-classify-by-parent", methods=["GET", "POST"])
async def auto_classify_by_parent():
    """Auto-approve/reject pending candidates based on their parent domain's existing status."""
    with get_conn() as conn:
        decided = conn.execute(
            "SELECT domain, status FROM tracker_candidates WHERE status IN ('approved','rejected')"
        ).fetchall()
        pending = conn.execute(
            "SELECT id, domain FROM tracker_candidates WHERE status='pending'"
        ).fetchall()

    approved_parents = {_parent(r["domain"]) for r in decided if r["status"] == "approved"}
    rejected_parents = {_parent(r["domain"]) for r in decided if r["status"] == "rejected"}

    to_approve = [r["id"] for r in pending if _parent(r["domain"]) in approved_parents]
    to_reject  = [r["id"] for r in pending if _parent(r["domain"]) in rejected_parents
                  and r["id"] not in to_approve]

    with get_conn() as conn:
        if to_approve:
            conn.executemany(
                "UPDATE tracker_candidates SET status='approved' WHERE id=?",
                [(i,) for i in to_approve]
            )
        if to_reject:
            conn.executemany(
                "UPDATE tracker_candidates SET status='rejected' WHERE id=?",
                [(i,) for i in to_reject]
            )

    return {"ok": True, "auto_approved": len(to_approve), "auto_rejected": len(to_reject)}


@app.api_route("/api/clean", methods=["GET", "POST"])
async def clean_candidates():
    """Remove pending candidates that would be excluded by current extractor rules."""
    from urllib.parse import urlparse
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, domain, url_example FROM tracker_candidates WHERE status='pending'"
        ).fetchall()

        removed = 0
        for row in rows:
            domain = row["domain"]
            url = row["url_example"]
            parsed = urlparse(url)

            should_remove = (
                _is_esp_click_domain(domain)
                or bool(_NON_IMAGE_EXT_RE.search(parsed.path))
            )

            if should_remove:
                conn.execute("DELETE FROM tracker_candidates WHERE id=?", (row["id"],))
                removed += 1

    return {"ok": True, "removed": removed}


@app.get("/api/candidates")
async def list_candidates(status: str = None):
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tracker_candidates WHERE status=? ORDER BY found_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tracker_candidates ORDER BY found_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/export/full")
async def export_full():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tracker_candidates WHERE status='approved' ORDER BY found_at"
        ).fetchall()
    return JSONResponse(
        content=[dict(r) for r in rows],
        headers={"Content-Disposition": "attachment; filename=mailtrack_hunter_full.json"},
    )


_DEDICATED_TRACKER_PREFIXES = {
    "t", "trk", "track", "open", "pixel", "beacon", "spy", "wf", "e", "mltrk", "r"
}


def _make_url_filter(domain: str, url_example: str) -> str:
    """Block at domain level for dedicated tracker subdomains; at path level otherwise."""
    from urllib.parse import urlparse
    prefix = domain.split(".")[0].lower()
    if prefix in _DEDICATED_TRACKER_PREFIXES:
        return f"||{domain}^"
    parsed = urlparse(url_example)
    return f"||{domain}{parsed.path}^"


@app.get("/api/export/rules")
async def export_rules():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT domain, url_example FROM tracker_candidates WHERE status='approved' ORDER BY found_at"
        ).fetchall()

    rules = []
    for i, row in enumerate(rows, start=100):
        rules.append({
            "id": i,
            "priority": 2,
            "action": {"type": "block"},
            "condition": {
                "urlFilter": _make_url_filter(row["domain"], row["url_example"]),
                "resourceTypes": ["image", "ping", "other", "xmlhttprequest"],
            },
        })

    return JSONResponse(
        content=rules,
        headers={"Content-Disposition": "attachment; filename=rules_extra.json"},
    )


def _fetch_block_mailtrack_rules() -> list:
    global _RULES_CACHE, _RULES_CACHE_AT
    if _RULES_CACHE is not None and (time.monotonic() - _RULES_CACHE_AT) < _RULES_CACHE_TTL:
        return _RULES_CACHE
    try:
        with urllib.request.urlopen(_BLOCK_MAILTRACK_RULES_URL, timeout=5) as resp:
            data = json.loads(resp.read())
        _RULES_CACHE = data
        _RULES_CACHE_AT = time.monotonic()
        logging.info("block-mailtrack rules refreshed from GitHub (%d rules)", len(data))
    except Exception as exc:
        if _RULES_CACHE is not None:
            logging.warning(
                "Could not refresh block-mailtrack rules from GitHub (%s) — using stale cache (%d rules)",
                exc, len(_RULES_CACHE),
            )
        else:
            logging.error(
                "Could not fetch block-mailtrack rules from GitHub (%s) — already-blocked check disabled", exc
            )
    return _RULES_CACHE or []


def _already_blocked_domains() -> set[str]:
    domains: set[str] = set()
    for rule in _fetch_block_mailtrack_rules():
        url_filter = rule.get("condition", {}).get("urlFilter", "")
        if url_filter.startswith("||"):
            domain = url_filter[2:].split("^")[0].split("/")[0]
            domains.add(domain)
    return domains


def _rules_cache_info() -> dict:
    if _RULES_CACHE is None:
        return {"available": False, "count": 0, "age_minutes": None}
    age_minutes = int((time.monotonic() - _RULES_CACHE_AT) / 60)
    return {"available": True, "count": len(_RULES_CACHE), "age_minutes": age_minutes}


@app.get("/api/export/github-issue")
async def export_github_issue():
    already_blocked = _already_blocked_domains()

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, domain, url_example, claude_reasoning FROM tracker_candidates "
            "WHERE status='approved' AND github_proposed_at IS NULL ORDER BY found_at"
        ).fetchall()

    new_rows = [r for r in rows if r["domain"] not in already_blocked]
    skipped = len(rows) - len(new_rows)

    if not new_rows:
        msg = "No new approved candidates to propose."
        if skipped:
            msg += f" ({skipped} already covered by block-mailtrack rules.)"
        return JSONResponse({"ok": False, "message": msg}, status_code=400)

    ids = [r["id"] for r in new_rows]

    table_lines = "\n".join(
        f"| `{r['domain']}` | `{_make_url_filter(r['domain'], r['url_example'])}` | {r['claude_reasoning'] or ''} |"
        for r in new_rows
    )

    body = (
        f"## New tracker domains from Mailtrack Hunter\n\n"
        f"{len(new_rows)} new tracking domain(s) to add to `rules/rules.json`"
        + (f" ({skipped} already covered, skipped)" if skipped else "")
        + f":\n\n"
        f"| Domain | Proposed filter | Reason |\n"
        f"|--------|----------------|--------|\n"
        f"{table_lines}\n\n"
        f"*Generated by [Mailtrack Hunter]({GITHUB_URL})*"
    )

    title = f"Add {len(new_rows)} new tracker rule(s) from Mailtrack Hunter"
    issue_url = (
        f"{BLOCK_MAILTRACK_GITHUB}/issues/new"
        f"?title={urllib.parse.quote(title)}"
        f"&body={urllib.parse.quote(body)}"
    )

    return {"ok": True, "url": issue_url, "count": len(new_rows), "skipped": skipped, "ids": ids}


@app.post("/api/candidates/record-issue")
async def record_issue(payload: dict):
    issue_id = int(payload.get("issue_id", 0))
    ids = payload.get("ids")
    if not issue_id or not ids:
        return JSONResponse({"ok": False, "message": "issue_id and ids required"}, status_code=400)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.executemany(
            "UPDATE tracker_candidates SET github_proposed_at=?, github_issue_id=? WHERE id=?",
            [(now, issue_id, id_) for id_ in ids],
        )
    return {"ok": True}


@app.post("/api/candidates/reset-proposed")
async def reset_proposed(ids: list[int]):
    with get_conn() as conn:
        conn.executemany(
            "UPDATE tracker_candidates SET github_proposed_at=NULL, github_issue_id=NULL WHERE id=?",
            [(id_,) for id_ in ids],
        )
    return {"ok": True, "reset": len(ids)}


@app.post("/api/scan")
async def trigger_scan():
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, scan)
    return {"ok": True, "message": "Scan triggered"}


@app.get("/api/status")
async def status():
    with get_conn() as conn:
        last = conn.execute(
            "SELECT * FROM scan_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "last_scan":        dict(last) if last else None,
            "total_processed":  conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0],
            "total_candidates": conn.execute("SELECT COUNT(*) FROM tracker_candidates").fetchone()[0],
        }
