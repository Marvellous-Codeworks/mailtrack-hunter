import json
import os
import re
import anthropic

_client = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))
    return _client


MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

_SYSTEM = """You are an expert in email privacy and tracking pixels.
Analyse image URLs extracted from commercial emails and identify tracking pixels.

Tracking pixels are typically:
- Transparent 1x1 images used to detect when an email is opened
- URLs with paths like /open, /track, /pixel, /beacon, /t/
- URLs containing tracking parameters (uid=, mid=, cid=, eid=)
- Served from domains whose sole purpose is email open tracking

Respond ONLY with a valid JSON array. Each element must have:
  "url"        – the original URL
  "domain"     – the registrable domain (e.g. "tracker.example.com")
  "is_tracker" – boolean
  "confidence" – "high", "medium", or "low"
  "reason"     – one concise sentence

Include ONLY entries where is_tracker is true and confidence is high or medium.
If none qualify, return an empty array []."""

_JSON_RE = re.compile(r"\[.*\]", re.DOTALL)


def classify_urls(urls: list[str]) -> list[dict]:
    if not urls:
        return []

    url_list = "\n".join(f"- {u}" for u in urls[:30])

    response = get_client().messages.create(
        model=MODEL,
        max_tokens=1024,
        system=_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Analyse these image URLs:\n\n{url_list}",
        }],
    )

    raw = response.content[0].text.strip()

    match = _JSON_RE.search(raw)
    if not match:
        return []

    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return []
