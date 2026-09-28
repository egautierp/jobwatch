"""Read LinkedIn job alert emails from Gmail and turn them into job records.

LinkedIn does not allow scraping, but it emails job alerts you set up. This
reads those emails from your own inbox over IMAP, using the same Gmail app
password as the daily digest.
"""
import datetime as dt
import email
import imaplib
import re
from email.header import decode_header

ALERT_SENDER = "jobalerts-noreply@linkedin.com"
JOB_LINK = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", re.I)
SKIP_LINE = re.compile(
    r"^(view job|apply with|apply|easy apply|actively recruiting|this company is actively hiring|"
    r"promoted|be an early applicant|your job alert|(\d+ )?new jobs? match|"
    r"\d+ (school alumni|connections?|applicants?|alumni)|see all jobs|job search smarter)", re.I)


def _text_part(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", "replace")
    return ""


def _all_parts(msg):
    """Plain text and HTML together; some LinkedIn emails only link the job in HTML."""
    out = []
    for part in msg.walk():
        if part.get_content_type() in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True) or b""
            out.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
    return "\n".join(out)


def parse_alert(body):
    """Pull title, company and location for each job in a plain text alert."""
    jobs, block = [], []
    for raw in body.splitlines():
        line = raw.strip()
        m = JOB_LINK.search(line)
        if m:
            info = [x for x in block if not SKIP_LINE.match(x) and "http" not in x]
            if info:
                # Each card lists title, company and location, then extras.
                head = (info[:3] + ["", "", ""])[:3]
                title, company, location = head
                jobs.append({
                    "title": title, "company": company, "location": location,
                    "url": f"https://www.linkedin.com/jobs/view/{m.group(1)}/",
                })
            block = []
        elif line and not set(line) <= set("-=_ "):
            block.append(line)
        elif set(line) and set(line) <= set("-=_ "):
            block = []
    return jobs


HIRING_SUBJECT = re.compile(r"^(.+?) is hiring for (.+)$", re.I)
POSTER = re.compile(r"Directly message ([A-Z][\w'\-]+(?: [A-Z][\w'\-]+)?) to stand out")


def _subject(msg):
    parts = []
    for text, enc in decode_header(msg.get("Subject", "")):
        parts.append(text.decode(enc or "utf-8", "replace") if isinstance(text, bytes) else text)
    return " ".join("".join(parts).split())


def parse_hiring(subject, body):
    """LinkedIn's "Company is hiring for Role" emails: one job each."""
    m = HIRING_SUBJECT.match(subject)
    link = JOB_LINK.search(body)
    if not (m and link):
        return []
    # These are LinkedIn's own suggestions, not your saved searches, so the
    # usual role keywords still apply to them.
    job = {"title": m.group(2).strip(), "company": m.group(1).strip(), "location": "",
           "url": f"https://www.linkedin.com/jobs/view/{link.group(1)}/", "trusted": False}
    poster = POSTER.search(body)
    if poster:
        job["contact"] = {"name": poster.group(1)}
    return [job]


def _folders(box):
    """Gmail's archive and spam folder names depend on the account language."""
    _, listing = box.list()
    found = {}
    for f in listing or []:
        line = f.decode(errors="replace")
        name = line.split(' "/" ')[-1]
        if "\\All" in line:
            found["all"] = name
        elif "\\Junk" in line:
            found["spam"] = name
    found.setdefault("all", '"[Gmail]/All Mail"')
    return [found["all"]] + ([found["spam"]] if "spam" in found else [])


SENDERS = ["jobalerts-noreply@linkedin.com", "messages-noreply@linkedin.com",
           "linkedin@em.linkedin.com"]


def fetch(user, password, days=7):
    """Read LinkedIn job emails from All Mail and Spam for the last few days."""
    since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%b-%Y")
    box = imaplib.IMAP4_SSL("imap.gmail.com")
    box.login(user, password)
    found = {}
    for folder in _folders(box):
        box.select(folder, readonly=True)
        for sender in SENDERS:
            _, data = box.search(None, f'(FROM "{sender}" SINCE {since})')
            for num in (data[0] or b"").split():
                _, msg_data = box.fetch(num, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                jobs = (parse_alert(_text_part(msg)) if sender == ALERT_SENDER
                        else parse_hiring(_subject(msg), _all_parts(msg)))
                for j in jobs:
                    found[j["url"]] = j
    box.logout()
    return list(found.values())
