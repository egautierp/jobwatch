"""Recognise the same role across sources, URL changes and reposts."""
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING = re.compile(
    r"^(utm_\w+|trackingid|trk|refid|ref|source|src|gh_src|lever-source|"
    r"lever-origin|mode|sid|cid|campaign|fbclid|gclid|mc_\w+)$", re.I)

LEGAL = re.compile(r"\b(llc|ltd|limited|plc|lp|llp|inc|sa|ag|gmbh|ab|as|bv|"
                   r"sarl|uk|europe|group|holdings)\b")

NOISE_WORDS = re.compile(
    r"\b(london|united kingdom|uk|england|gb|remote|hybrid|m f d|m w d|f m d|"
    r"permanent|full time|fixed term|maternity cover|city of london|greater london)\b")
STOP = {"and", "of", "the", "a", "an", "for", "to", "in", "at", "with"}


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def norm_firm(name):
    return re.sub(r"\s+", " ", LEGAL.sub(" ", norm(name))).strip()


def norm_title(title):
    t = NOISE_WORDS.sub(" ", norm(title))
    t = re.sub(r"\bsnr\b", "senior", t)
    t = re.sub(r"\bassoc\b", "associate", t)
    t = re.sub(r"\bvp\b", "vice president", t)
    return re.sub(r"\s+", " ", t).strip()


def canonical_url(url):
    p = urlparse(url or "")
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not TRACKING.match(k)]
    return urlunparse(p._replace(query=urlencode(q), fragment=""))


def fingerprint(firm, title):
    return f"{norm_firm(firm)}|{norm_title(title)}"


def role_id(firm, title):
    return hashlib.sha1(fingerprint(firm, title).encode()).hexdigest()[:12]


def match_firm(company, known):
    """Map a company name from LinkedIn to one of the configured names."""
    c = norm_firm(company)
    if not c:
        return company
    for name in known:
        k = norm_firm(name)
        if len(k) >= 4 and (c == k or c.startswith(k + " ") or k.startswith(c + " ")):
            return name
    return company


def similar(a, b):
    """Share of the shorter title's words found in the other title (0 to 1)."""
    x = set(norm_title(a).split()) - STOP
    y = set(norm_title(b).split()) - STOP
    if len(x & y) < 3:
        return 0.0
    return len(x & y) / min(len(x), len(y))
