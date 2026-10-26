"""Application-reply tracker: read recent employer emails from Gmail (IMAP, same app password
as the sender) and flag the ones that need action. Nothing here is sent to any AI service.
"""

import email, html, imaplib, os, re
from email.header import decode_header, make_header

# Gmail search, same syntax as the Gmail search box. Excludes job-alert newsletters.
# Must stay plain ASCII: imaplib sends the search string as ASCII (an umlaut crashed the first live run).
QUERY = (
    'newer_than:2d -from:me '
    '(bewerbung OR application OR applying OR absage OR interview OR assessment OR "online test" '
    'OR praktikum OR internship OR "your candidacy" OR einladung OR vorstellungsgesprach) '
    '-from:(jobalert.indeed.com OR jobagent.stepstone.de OR linkedin.com OR hokify.com OR jobmensa.de '
    'OR medium.com OR expatrio.com OR datacamp.com OR substack.com OR beehiiv.com OR godofprompt.ai)'
)

FLAGS = [   # (label, colour, patterns) - first match wins, so order = priority
    ("ACTION: TEST / DEADLINE", "#b91c1c", r"online.?test|assessment|innerhalb .{0,20}tage|within .{0,20}days|frist|deadline|complete .{0,30}test|coding challenge|case study"),
    # Invitations only: confirmation emails often mention "interview" in their process text.
    ("INTERVIEW",               "#15803d", r"invite you|invitation to (an |a )?(interview|call|meeting)|einladung zu|einladung zum|"
                                           r"laden (sie|dich) .{0,30}ein|schedule (an |a )?(interview|call)|"
                                           r"vorstellungsgespräch am|kennenlerngespräch|book a (time|slot)|terminvorschlag"),
    ("REJECTION",               "#6b7280", r"leider|unfortunately|absage|unable to consider|not able to offer|keine position anbieten|nicht berücksichtigen|decided to move forward with other"),
    ("RECEIVED",                "#2563eb", r"eingegangen|received your application|we received your application|"
                                           r"thank you for (your )?appl|vielen dank für (deine|ihre) bewerbung|bewerbung erhalten"),
]


def _dec(v) -> str:
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def _body_text(msg) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    for p in parts:
        if p.get_content_type() in ("text/plain", "text/html") and not p.get("Content-Disposition", "").startswith("attachment"):
            try:
                t = p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace")
            except Exception:
                continue
            if p.get_content_type() == "text/html":
                t = re.sub(r"(?is)<(style|script|head)\b.*?</\1>", " ", t)   # CSS was leaking into snippets
                t = re.sub(r"<[^>]+>", " ", t)
            return re.sub(r"\s+", " ", html.unescape(t)).strip()
    return ""


def classify(subject: str, body: str):
    text = f"{subject} {body}".lower()
    flags = dict((label, (colour, pat)) for label, colour, pat in FLAGS)
    # A confirmation subject ("We have received your application") wins over rejection words in the
    # boilerplate (26 Oct 2026: BSH's confirmation was flagged REJECTION). Tests and invitations still win.
    if re.search(flags["RECEIVED"][1], subject.lower()) and not any(
            re.search(flags[k][1], text) for k in ("ACTION: TEST / DEADLINE", "INTERVIEW")):
        return "RECEIVED", flags["RECEIVED"][0]
    for label, colour, pat in FLAGS:
        if re.search(pat, text):
            return label, colour
    return "OTHER", "#374151"


def fetch(user=None, password=None, imap_factory=imaplib.IMAP4_SSL, limit=25) -> list:
    user = user or os.environ["GMAIL_USER"]
    password = password or os.environ["GMAIL_APP_PASSWORD"]
    m = imap_factory("imap.gmail.com")
    try:
        m.login(user, password)
        m.select("INBOX", readonly=True)                    # read-only: never marks anything as read
        typ, data = m.search(None, "X-GM-RAW", '"' + QUERY.replace('"', '\\"') + '"')
        ids = (data[0] or b"").split()[-limit:]
        out = []
        for i in reversed(ids):
            typ, msg_data = m.fetch(i, "(BODY.PEEK[])")
            msg = email.message_from_bytes(msg_data[0][1])
            subject, sender = _dec(msg["Subject"]), _dec(msg["From"])
            body = _body_text(msg)
            label, colour = classify(subject, body)
            out.append({"subject": subject, "from": sender, "date": msg["Date"] or "",
                        "snippet": body[:220], "label": label, "colour": colour})
        return out
    finally:
        try:
            m.logout()
        except Exception:
            pass


def render_html(items: list) -> str:
    if not items:
        return ("<h2 style='font-family:Arial'>Application inbox (last 48h)</h2>"
                "<p style='font-family:Arial'>No new replies from employers.</p>")
    order = {f[0]: i for i, f in enumerate(FLAGS)}
    items = sorted(items, key=lambda x: order.get(x["label"], 99))
    rows = "".join(f"""
<tr><td style="padding:6px 8px;vertical-align:top"><span style="background:{x['colour']};color:#fff;padding:2px 7px;border-radius:9px;font-size:12px;white-space:nowrap">{html.escape(x['label'])}</span></td>
<td style="padding:6px 8px"><b>{html.escape(x['subject'][:120])}</b><br>
<span style="color:#6b7280;font-size:12px">{html.escape(x['from'][:80])}</span><br>
<span style="font-size:13px">{html.escape(x['snippet'])}&hellip;</span></td></tr>""" for x in items)
    urgent = sum(1 for x in items if x["label"].startswith("ACTION") or x["label"] == "INTERVIEW")
    head = (f"<p style='color:#b91c1c;font-family:Arial'><b>{urgent} email(s) need action - open them first.</b></p>"
            if urgent else "")
    return ("<h2 style='font-family:Arial'>Application inbox (last 48h)</h2>" + head +
            f"<table style='font-family:Arial;border-collapse:collapse;font-size:14px'>{rows}</table>")
