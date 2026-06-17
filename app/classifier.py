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

Your task is to identify SILENT TRACKING PIXELS — invisible images loaded automatically
when an email is opened, used solely to notify the sender that the email was read.

A silent tracking pixel:
- Is a 1x1 (or 0x0) transparent image, never visible to the user
- Is served from a domain or subdomain dedicated exclusively to open-tracking
- Has no other purpose: it does not handle clicks, redirects, or serve content

Do NOT flag:
- Click-tracking or redirect URLs (e.g. click.*, links.*, go.*, r.*, redirect.*)
- Generic ESP endpoints used for both pixels AND link redirection
- Decorative or functional images from email service providers
- Anything where blocking the domain would break links or visible content

Respond ONLY with a valid JSON array. Each element must have:
  "url"        – the original URL
  "domain"     – the full subdomain to block (e.g. "t.example.com", not just "example.com")
  "is_tracker" – boolean
  "confidence" – "high", "medium", or "low"
  "reason"     – one concise sentence explaining why it is a silent pixel

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
