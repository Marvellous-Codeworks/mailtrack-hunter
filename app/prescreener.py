COMMERCIAL_HEADERS = {
    "list-unsubscribe",
    "list-id",
    "list-post",
    "list-archive",
    "list-help",
    "x-campaign-id",
    "x-mailchimp-id",
    "x-mc-user",
    "x-contactlab",
    "x-brevo",
    "x-sendinblue",
    "x-hubspot",
    "x-marketo",
    "x-pardot",
    "x-salesforce-mc",
}

BULK_PRECEDENCE = {"bulk", "list", "junk"}

KNOWN_ESP_KEYWORDS = [
    "mailchimp", "contactlab", "sendinblue", "brevo", "hubspot",
    "marketo", "pardot", "mailjet", "sendgrid", "constantcontact",
    "campaignmonitor", "klaviyo", "activecampaign", "getresponse",
    "aweber", "convertkit", "drip", "omnisend", "salesmanago",
]


def is_commercial(headers: dict) -> bool:
    """Return True if email headers suggest a newsletter / bulk / commercial send."""
    for h in COMMERCIAL_HEADERS:
        if h in headers:
            return True

    if headers.get("precedence", "").strip().lower() in BULK_PRECEDENCE:
        return True

    mailer = headers.get("x-mailer", "").lower()
    for kw in KNOWN_ESP_KEYWORDS:
        if kw in mailer:
            return True

    return False
