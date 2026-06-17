import asyncio
import logging
import os
from contextlib import asynccontextmanager

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

    last_scan = dict(last_scan_row) if last_scan_row else None

    return templates.TemplateResponse("index.html", {
        "request":      request,
        "stats":        stats,
        "last_scan":    last_scan,
        "candidates":   candidates,
        "poll_interval": int(os.getenv("POLL_INTERVAL_MINUTES", 30)),
        "version":      APP_VERSION,
        "github_url":   GITHUB_URL,
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


@app.get("/api/export/rules")
async def export_rules():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT domain FROM tracker_candidates WHERE status='approved' ORDER BY found_at"
        ).fetchall()

    rules = []
    for i, row in enumerate(rows, start=100):
        rules.append({
            "id": i,
            "priority": 2,
            "action": {"type": "block"},
            "condition": {
                "urlFilter": f"||{row['domain']}^",
                "resourceTypes": ["image", "ping", "other", "xmlhttprequest"],
            },
        })

    return JSONResponse(
        content=rules,
        headers={"Content-Disposition": "attachment; filename=rules_extra.json"},
    )


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
