"""Pull a named contact, email and phone from a job advert, where one is given.

Recruiter adverts usually name the consultant. Firm adverts rarely do, so most
direct roles will have no contact and the page offers a LinkedIn people search
instead.
"""
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from . import sources

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?:\+44\s?\(?0?\)?\s?|\b0)(?:\d[\s]?){9,10}\b")
NAME_LABEL = re.compile(
    r"(?:consultant|contact|recruiter|posted by|hiring manager|get in touch with|"
    r"speak to|call|email)\s*[:\-]?\s*([A-Z][a-z]+(?:\s[A-Z][a-z'\-]+){1,2})\b")
NOT_PERSON = re.compile(
    r"^(noreply|no-reply|donotreply|privacy|dataprotection|data\.protection|gdpr|dpo|"
    r"unsubscribe|webmaster|support|marketing|press|media|complaints|accounts)", re.I)
BAD_NAME = {"Our Team", "The Team", "Apply Now", "Real Estate", "Asset Management",
            "Human Resources", "Find Out", "Click Here", "Contact Us"}


def _workday_api(url):
    p = urlparse(url)
    tenant = p.netloc.split(".")[0]
    path = re.sub(r"^/[a-z]{2}-[A-Z]{2}", "", p.path)
    return f"https://{p.netloc}/wday/cxs/{tenant}{path}"


def page_text(url):
    """Return (visible text, list of mailto emails, list of tel numbers)."""
    if "myworkdayjobs.com" in url:
        d = sources._get(_workday_api(url)).json()
        html = (d.get("jobPostingInfo") or {}).get("jobDescription", "")
    else:
        html = sources._get(url).text
    soup = BeautifulSoup(html, "html.parser")
    mailto = [a["href"][7:].split("?")[0] for a in soup.select('a[href^="mailto:"]')]
    tel = [a["href"][4:] for a in soup.select('a[href^="tel:"]')]
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup
    return main.get_text(" ", strip=True), mailto, tel


def extract(text, mailto=(), tel=()):
    emails = []
    for e in list(mailto) + EMAIL.findall(text):
        e = e.strip().rstrip(".").lower()
        if e not in emails and not NOT_PERSON.match(e) and not e.endswith((".png", ".jpg")):
            emails.append(e)
    phones = [re.sub(r"\s+", " ", p).strip() for p in list(tel) + PHONE.findall(text)]
    name = ""
    for m in NAME_LABEL.finditer(text):
        if m.group(1) not in BAD_NAME:
            name = m.group(1)
            break
    if not name and emails:
        local = emails[0].split("@")[0]
        parts = re.split(r"[._]", local)
        if len(parts) == 2 and all(len(x) > 1 and x.isalpha() for x in parts):
            name = " ".join(x.capitalize() for x in parts)
    out = {}
    if name:
        out["name"] = name
    if emails:
        out["email"] = emails[0]
    if phones:
        out["phone"] = phones[0]
    return out


def find(url):
    if "linkedin.com" in url:
        return {}
    text, mailto, tel = page_text(url)
    return extract(text, mailto, tel)
