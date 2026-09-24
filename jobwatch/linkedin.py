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


def _all_mail(box):
    """Gmail's archive folder name depends on the account language, so find it."""
    _, folders = box.list()
    for f in folders or []:
        line = f.decode(errors="replace")
        if "\\All" in line:
            return line.split(' "/" ')[-1]
    return '"[Gmail]/All Mail"'


def fetch(user, password, days=7):
    since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%b-%Y")
    box = imaplib.IMAP4_SSL("imap.gmail.com")
    box.login(user, password)
    box.select(_all_mail(box), readonly=True)
    _, data = box.search(None, f'(FROM "{ALERT_SENDER}" SINCE {since})')
    found = {}
    for num in data[0].split():
        _, msg_data = box.fetch(num, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        for j in parse_alert(_text_part(msg)):
            found[j["url"]] = j
    box.logout()
    return list(found.values())
