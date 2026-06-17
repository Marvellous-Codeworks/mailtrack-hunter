import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup

KNOWN_CDN_SUFFIXES = {
    "googleusercontent.com", "googleapis.com", "gstatic.com",
    "gravatar.com", "wp.com", "wordpress.com",
    "akamai.net", "cloudfront.net", "fastly.net",
    "amazonaws.com", "azureedge.net", "azurefd.net",
    "imgix.net", "cloudinary.com",
    "licdn.com", "twimg.com", "fbcdn.net",
    "ytimg.com", "ggpht.com",
}

KNOWN_TRACKER_DOMAINS = {
    "t.mailtrack.io", "mltrk.io",
    "trk.yesware.com", "t.yesware.com",
    "t.sidekickopen.com", "t2.sidekickopen.com", "t3.sidekickopen.com",
    "t4.sidekickopen.com", "t5.sidekickopen.com",
    "s.bananatag.com", "d.bananatag.com", "r.bananatag.com",
    "t.salesloft.com", "click.salesloft.com",
    "t.outreach.io",
    "email.mixmax.com",
    "mailfoogae.appspot.com",
    "track.apollo.io", "open.apollo.io",
    "lmltrck.com",
    "trk.gmass.co", "open.gmass.co",
    "track.reply.io",
    "mgf.boomerangapp.com",
    "t.lavender.ai", "track.lavender.ai",
    "tracking.clearbit.com",
    "t.groove.co",
}

_TRACKER_PATH_RE = re.compile(
    r"/(open|track|pixel|beacon|spy|wf/open|e/open|t)(/|\?|$)",
    re.IGNORECASE,
)
_TRACKER_PARAM_RE = re.compile(r"[?&](uid|mid|cid|eid|track|pixel|beacon)=", re.IGNORECASE)


def get_html_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
    elif msg.get_content_type() == "text/html":
        charset = msg.get_content_charset() or "utf-8"
        return msg.get_payload(decode=True).decode(charset, errors="replace")
    return ""


def extract_candidate_urls(html: str) -> list[str]:
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    candidates = []

    for img in soup.find_all("img", src=True):
        url = img.get("src", "").strip()
        if not url.startswith(("http://", "https://")):
            continue

        parsed = urlparse(url)
        domain = parsed.netloc.lower().lstrip("www.")

        if any(domain.endswith(cdn) for cdn in KNOWN_CDN_SUFFIXES):
            continue
        if domain in KNOWN_TRACKER_DOMAINS:
            continue

        candidates.append(url)

    return candidates


def is_likely_tracker(url: str) -> bool:
    parsed = urlparse(url)
    if _TRACKER_PATH_RE.search(parsed.path):
        return True
    if _TRACKER_PARAM_RE.search(parsed.query):
        return True
    return False
