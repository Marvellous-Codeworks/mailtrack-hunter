# Mailtrack Hunter

A self-hosted Docker container that monitors your inbox via IMAP, identifies commercial emails, and uses the Claude API to discover unknown email tracker domains — feeding the [Block Mailtrack](https://github.com/Marvellous-Codeworks/block-mailtrack) browser extension's blocklist.

---

## How it works

1. **Connects to your mail server via IMAP** — monitors configured folders (INBOX, Junk, or any others)
2. **Pre-screens by header** — skips personal emails; only processes newsletters and commercial mail (free, no API call)
3. **Extracts image URLs** — parses HTML body, discards known CDNs and already-blocked domains (free)
4. **Classifies with Claude** — sends only unknown suspicious URLs to the Claude API (Haiku, minimal cost)
5. **Web UI for review** — approve or reject each candidate before it goes anywhere near the extension

On first run, the last `IMAP_INITIAL_LIMIT` emails per folder are scanned. From then on, only new unseen messages are checked. The state persists across container restarts via a mounted SQLite volume.

## Quick start

### Docker Compose (recommended)

```bash
cp .env.example .env
# edit .env with your IMAP credentials and Claude API key
docker compose up -d
```

Open `http://localhost:8000` in your browser.

### Docker Run

```bash
docker build -t mailtrack-hunter .

docker run -d \
  --name mailtrack-hunter \
  -p 8000:8000 \
  -v mailtrack_data:/data \
  -e IMAP_HOST=mail.example.com \
  -e IMAP_PORT=993 \
  -e IMAP_USER=your@email.com \
  -e IMAP_PASSWORD=yourpassword \
  -e IMAP_FOLDERS=INBOX,Junk \
  -e IMAP_INITIAL_LIMIT=50 \
  -e CLAUDE_API_KEY=sk-ant-... \
  -e CLAUDE_MODEL=claude-haiku-4-5-20251001 \
  -e POLL_INTERVAL_MINUTES=30 \
  --restart unless-stopped \
  mailtrack-hunter
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `IMAP_HOST` | — | IMAP server hostname |
| `IMAP_PORT` | `993` | IMAP port |
| `IMAP_USER` | — | IMAP username |
| `IMAP_PASSWORD` | — | IMAP password |
| `IMAP_USE_SSL` | `true` | Use SSL/TLS |
| `IMAP_FOLDERS` | `INBOX,Junk` | Comma-separated list of folders to monitor |
| `IMAP_INITIAL_LIMIT` | `50` | Emails to scan per folder on first run |
| `CLAUDE_API_KEY` | — | Anthropic API key |
| `CLAUDE_MODEL` | `claude-haiku-4-5-20251001` | Claude model to use |
| `POLL_INTERVAL_MINUTES` | `30` | How often to check for new mail |
| `APP_PORT` | `8000` | Host port (Docker Compose only) |

## Web UI

- **Dashboard** — processed email count, pending/approved/rejected candidates, last scan info
- **Candidates table** — filterable by status, with domain, example URL, source sender, and Claude's reasoning
- **Scan now** — triggers an immediate scan without waiting for the next interval
- **Export full JSON** — all approved candidates with full metadata (domain, URL, source, timestamp, reasoning)
- **Export rules.json** — approved candidates formatted as `declarativeNetRequest` rules, ready to merge into Block Mailtrack's `rules/rules.json`

## Feeding results back to Block Mailtrack

1. Review and approve candidates in the web UI
2. Click **Export rules.json** — this downloads a `rules_extra.json` file
3. Merge the entries into `rules/rules.json` in the Block Mailtrack repo (IDs start at 100 to avoid conflicts with the base rules)
4. Open a PR or commit directly

## License

See [LICENSE](LICENSE).
