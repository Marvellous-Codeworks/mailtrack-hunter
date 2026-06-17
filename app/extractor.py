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

# Subdomains used by ESPs for click-tracking and link redirection.
# These are NOT silent pixels — they handle user clicks, blocking them
# would break links in emails. Exclude entirely.
_ESP_CLICK_PREFIX_RE = re.compile(
    r"^(click|clicks|links?|go|r|redirect|track|cta|email|mail|news|nl|send|sg)\.",
    re.IGNORECASE,
)

# Pixel path endings that are unambiguously silent tracking images.
_PIXEL_PATH_RE = re.compile(
    r"/(pixel|beacon|spy|spacer|blank|1x1|track\.(gif|png)|open\.(gif|png)|trk\.(gif|png))$",
    re.IGNORECASE,
)

# File extensions that are clearly not images (click redirects, web pages).
_NON_IMAGE_EXT_RE = re.compile(r"\.(aspx?|php|html?|cfm|jsp|do)(\?|$)", re.IGNORECASE)


_TINY_DIM_RE = re.compile(r"^[0-4]$")  # 0–4 px counts as "tiny"
_HIDDEN_STYLE_RE = re.compile(
    r"(display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0|"
    r"(width|height)\s*:\s*[0-4]px)",
    re.IGNORECASE,
)


def _is_pixel_tag(img) -> bool:
    """Return True if the <img> tag looks like a silent tracking pixel.

    Covers: explicit tiny dimensions (0-4 px), CSS-hidden images of any size,
    and URLs with unambiguous pixel-endpoint paths.
    """
    # Explicit tiny width or height attribute (0–4 px).
    for attr in ("width", "height"):
        val = img.get(attr, "").strip().rstrip("px").strip()
        if _TINY_DIM_RE.match(val):
            return True

    # CSS hiding (any dimension can hide a pixel).
    style = img.get("style", "")
    if _HIDDEN_STYLE_RE.search(style):
        return True

    # URL path is an unambiguous pixel endpoint.
    url = img.get("src", "")
    if _PIXEL_PATH_RE.search(urlparse(url).path):
        return True

    return False


def _is_esp_click_domain(domain: str) -> bool:
    """Return True for ESP click-tracking / redirect subdomains."""
    return bool(_ESP_CLICK_PREFIX_RE.match(domain))


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
    """Return image URLs that look like silent tracking pixels and are not yet known."""
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

        # Skip known-safe domains.
        if any(domain.endswith(cdn) for cdn in KNOWN_CDN_SUFFIXES):
            continue

        # Skip already-covered tracker domains.
        if domain in KNOWN_TRACKER_DOMAINS:
            continue

        # Skip ESP click-tracking / redirect subdomains — not pixels.
        if _is_esp_click_domain(domain):
            continue

        # Skip URLs that are clearly web pages or redirects, not images.
        if _NON_IMAGE_EXT_RE.search(parsed.path):
            continue

        # Only proceed if the tag itself signals a silent pixel.
        if not _is_pixel_tag(img):
            continue

        candidates.append(url)

    return candidates


def is_likely_tracker(url: str) -> bool:
    """Secondary hint used to prioritise candidates before the Claude call."""
    parsed = urlparse(url)
    return bool(_PIXEL_PATH_RE.search(parsed.path))
