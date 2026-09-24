"""Fetch job postings from careers pages.

Most investment managers host their jobs on an applicant tracking system (ATS)
such as Workday or Greenhouse. These expose clean JSON feeds, so we detect the
ATS behind each careers page and read the feed directly. Anything we cannot
identify falls back to a link scan of the page itself.
"""
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 30

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"})


def _get(url, **kw):
    r = session.get(url, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def _job(title, url, location="", posted="", department=""):
    return {
        "title": " ".join((title or "").split()),
        "url": url,
        "location": " ".join((location or "").split()),
        "posted": posted or "",
        "department": department or "",
    }


# ---------------------------------------------------------------- detection

PATTERNS = [
    ("workday", re.compile(
        r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:wday/cxs/[\w-]+/)?"
        r"(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", re.I)),
    ("greenhouse", re.compile(
        r"(?:boards|job-boards)(?:-api)?\.greenhouse\.io/"
        r"(?:embed/job_board(?:/js)?\?for=|v1/boards/)?([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(eu\.)?lever\.co/([\w-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)", re.I)),
    ("smartrecruiters", re.compile(
        r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)", re.I)),
    ("teamtailor", re.compile(r"([\w-]+)\.teamtailor\.com", re.I)),
    ("personio", re.compile(r"([\w-]+)\.jobs\.personio\.(de|com)", re.I)),
    ("recruitee", re.compile(r"([\w-]+)\.recruitee\.com", re.I)),
]

_IGNORE = {"embed", "v1", "js", "www", "api", "static", "assets", "cdn", "careers"}


def detect(text):
    """Return an ATS config dict from any text (URL or page HTML), or None."""
    for name, rx in PATTERNS:
        for m in rx.finditer(text):
            g = m.groups()
            if name == "workday":
                tenant, dc, site = g
                if site.lower() in {"wday", "cxs"}:
                    continue
                return {"ats": "workday", "tenant": tenant, "dc": dc, "site": site}
            if name == "lever":
                if g[1].lower() in _IGNORE:
                    continue
                return {"ats": "lever", "slug": g[1], "eu": bool(g[0])}
            if name == "personio":
                return {"ats": "personio", "slug": g[0], "tld": g[1]}
            slug = g[0]
            if slug.lower() in _IGNORE:
                continue
            return {"ats": name, "slug": slug}
    return None


def render(url):
    """Load a page in headless Chromium for JavaScript-built or bot-protected pages."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(user_agent=UA, ignore_https_errors=True,
                                      locale="en-GB")
        page = context.new_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        status = resp.status if resp else 0
        html = page.content()
        frames = "\n".join(f.url for f in page.frames)
        final = page.url
        browser.close()
    if status == 404:
        raise NotFound(url)
    return html + "\n" + frames, final


class NotFound(Exception):
    pass


def _swap_www(url):
    p = urlparse(url)
    host = p.netloc[4:] if p.netloc.startswith("www.") else "www." + p.netloc
    return p._replace(netloc=host).geturl()


def page_html(url, use_browser=False):
    """Return (html, final_url). Falls back to a real browser when a site
    blocks plain requests, and tries the other www form on certificate errors."""
    if use_browser:
        return render(url)
    try:
        r = session.get(url, timeout=TIMEOUT)
        if r.status_code == 404:
            raise NotFound(url)
        r.raise_for_status()
        return r.text, r.url
    except NotFound:
        raise
    except requests.exceptions.SSLError:
        try:
            r = _get(_swap_www(url))
            return r.text, r.url
        except Exception:
            return render(url)
    except (requests.exceptions.HTTPError, requests.exceptions.ConnectionError,
            requests.exceptions.Timeout):
        return render(url)


_CAREERS_LINK = re.compile(r"career|vacanc|join[ -]?us|work[ -]?(with|for)[ -]?us|"
                           r"opportunit|jobs|recruit", re.I)


def find_careers_link(html, base):
    soup = BeautifulSoup(html, "html.parser")
    best = None
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"]).split("#")[0]
        text = " ".join(a.get_text(" ").split())
        if href.startswith(("mailto:", "tel:")) or len(text) > 40:
            continue
        if _CAREERS_LINK.search(text) or _CAREERS_LINK.search(urlparse(href).path):
            if detect(href):
                return href  # a direct link to a hiring system is best
            best = best or href
    return best


# ----------------------------------------------------------------- adapters

def workday(c):
    host = f"{c['tenant']}.{c['dc']}.myworkdayjobs.com"
    api = f"https://{host}/wday/cxs/{c['tenant']}/{c['site']}/jobs"
    out, offset, total = [], 0, None
    while offset < 2000:
        r = session.post(
            api, timeout=TIMEOUT,
            json={"appliedFacets": {}, "limit": 20, "offset": offset,
                  "searchText": c.get("search", "")},
            headers={"Accept": "application/json"})
        r.raise_for_status()
        d = r.json()
        posts = d.get("jobPostings", [])
        if total is None:
            total = d.get("total", 0)
        for p in posts:
            out.append(_job(p.get("title"),
                            f"https://{host}/en-US/{c['site']}{p.get('externalPath', '')}",
                            p.get("locationsText"), p.get("postedOn")))
        offset += 20
        if not posts or offset >= (total or 0):
            break
        time.sleep(0.3)
    return out


def greenhouse(c):
    d = _get(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs").json()
    return [_job(j["title"], j["absolute_url"],
                 (j.get("location") or {}).get("name"),
                 (j.get("first_published") or j.get("updated_at") or "")[:10])
            for j in d.get("jobs", [])]


def lever(c):
    base = "api.eu.lever.co" if c.get("eu") else "api.lever.co"
    d = _get(f"https://{base}/v0/postings/{c['slug']}?mode=json").json()
    out = []
    for j in d:
        cat = j.get("categories") or {}
        posted = time.strftime("%Y-%m-%d", time.gmtime(j.get("createdAt", 0) / 1000))
        out.append(_job(j.get("text"), j.get("hostedUrl"), cat.get("location"),
                        posted, cat.get("department") or cat.get("team")))
    return out


def ashby(c):
    d = _get(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}").json()
    return [_job(j.get("title"), j.get("jobUrl"), j.get("location"),
                 (j.get("publishedAt") or "")[:10], j.get("department"))
            for j in d.get("jobs", [])]


def smartrecruiters(c):
    out, offset = [], 0
    while True:
        d = _get(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings",
                 params={"limit": 100, "offset": offset}).json()
        for j in d.get("content", []):
            loc = j.get("location") or {}
            out.append(_job(j.get("name"),
                            f"https://jobs.smartrecruiters.com/{c['slug']}/{j.get('id')}",
                            ", ".join(x for x in [loc.get("city"), loc.get("country")] if x),
                            (j.get("releasedDate") or "")[:10],
                            (j.get("department") or {}).get("label")))
        offset += 100
        if offset >= d.get("totalFound", 0):
            return out


def workable(c):
    out, token = [], None
    while True:
        body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        if token:
            body["token"] = token
        r = session.post(f"https://apply.workable.com/api/v3/accounts/{c['slug']}/jobs",
                         json=body, timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()
        for j in d.get("results", []):
            loc = j.get("location") or {}
            out.append(_job(j.get("title"),
                            f"https://apply.workable.com/{c['slug']}/j/{j.get('shortcode')}/",
                            ", ".join(x for x in [loc.get("city"), loc.get("country")] if x),
                            (j.get("published") or "")[:10], j.get("department")))
        token = d.get("nextPage")
        if not token:
            return out


def teamtailor(c):
    root = ET.fromstring(_get(f"https://{c['slug']}.teamtailor.com/jobs.rss").content)
    out = []
    for item in root.iter("item"):
        loc = ""
        for el in item:
            if el.tag.endswith("location") or el.tag.endswith("city"):
                loc = loc or (el.text or "")
        out.append(_job(item.findtext("title"), item.findtext("link"), loc,
                        item.findtext("pubDate", "")))
    return out


def teamtailor_feed(c):
    """Teamtailor boards on a firm's own domain publish the same RSS feed."""
    root = ET.fromstring(_get(c["feed"]).content)
    return [_job(i.findtext("title"), i.findtext("link"), "", i.findtext("pubDate", ""))
            for i in root.iter("item")]


def personio(c):
    base = f"https://{c['slug']}.jobs.personio.{c.get('tld', 'de')}"
    root = ET.fromstring(_get(f"{base}/xml?language=en").content)
    return [_job(p.findtext("name"), f"{base}/job/{p.findtext('id')}",
                 p.findtext("office"), (p.findtext("createdAt") or "")[:10],
                 p.findtext("department"))
            for p in root.iter("position")]


def recruitee(c):
    d = _get(f"https://{c['slug']}.recruitee.com/api/offers/").json()
    return [_job(o.get("title"), o.get("careers_url"), o.get("location"),
                 (o.get("published_at") or "")[:10], o.get("department"))
            for o in d.get("offers", [])]


_JOB_PATH = re.compile(
    r"/(jobs?|vacanc\w*|positions?|openings?|opportunit\w*|roles?|requisition)[/-]", re.I)
_NOT_JOB = re.compile(
    r"/(news|insights?|press|media|articles?|blogs?|events?|case-stud\w*|research|"
    r"stories|story|publications?|reports?|podcasts?|videos?|our-people|people|team|"
    r"leadership|about|investments?|portfolio|funds?|strateg\w*|sustainab\w*|contact)(/|$)",
    re.I)
_ROLE_WORD = re.compile(
    r"\b(analyst|associate|manager|director|vice president|vp|head of|officer|"
    r"executive|partner|principal|intern|controller|specialist|lead)\b", re.I)


def generic(c, html=None):
    """Scan a careers page for links that look like individual postings."""
    url = c["url"]
    if html is None:
        html, url = page_html(url, c.get("browser", False))
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        text = " ".join(a.get_text(" ").split())
        href = urljoin(url, a["href"]).split("#")[0]
        if not (6 <= len(text) <= 140) or href in seen:
            continue
        if href.rstrip("/") == url.rstrip("/") or href.startswith(("mailto:", "tel:")):
            continue
        path = urlparse(href).path
        # A posting has a job title (not a headline) and is not a news or team page.
        if len(text.split()) > 12 or not _ROLE_WORD.search(text):
            continue
        if _NOT_JOB.search(path) and not _JOB_PATH.search(path) and not detect(href):
            continue
        if True:
            seen.add(href)
            out.append(_job(text, href))
    return out


ADAPTERS = {
    "workday": workday, "greenhouse": greenhouse, "lever": lever, "ashby": ashby,
    "smartrecruiters": smartrecruiters, "workable": workable,
    "teamtailor": teamtailor, "teamtailor_feed": teamtailor_feed, "personio": personio, "recruitee": recruitee,
}


def _origin(url):
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


def _identify(url, html):
    found = detect(url) or detect(html)
    if not found and "teamtailor" in html.lower():
        found = {"ats": "teamtailor_feed", "feed": _origin(url) + "jobs.rss"}
    return found


def resolve(firm, cache, today):
    """Work out which adapter serves this firm, cached for 7 days.

    Returns (config, html, page_url). If the careers link is broken, the firm's
    home page is searched for its careers link. If a plain page shows no jobs,
    it is loaded again in a real browser.
    """
    if firm.get("ats_url"):
        found = detect(firm["ats_url"])
        if found:
            return found, None, firm["ats_url"]
    key = firm["careers_url"]
    hit = cache.get(key)
    if hit and hit.get("checked", "") >= today_minus(today, 7) and hit["config"]["ats"] != "generic":
        return hit["config"], None, hit.get("url", key)

    use_browser = firm.get("browser", False)
    try:
        host = urlparse(key).netloc
        if urlparse(key).path in ("", "/") and not host.startswith(("careers.", "jobs.")):
            raise NotFound(key)  # a home page: look for its careers link
        html, url = page_html(key, use_browser)
    except NotFound:
        home, _ = page_html(_origin(key), use_browser)
        link = find_careers_link(home, _origin(key))
        if not link:
            raise NotFound(f"{key} not found and no careers link on the home page")
        html, url = page_html(link, use_browser)

    found = _identify(url, html)
    if not found and not use_browser and not generic({"url": url}, html):
        try:
            html, url = render(url)
            found = _identify(url, html)
        except Exception:
            pass
    config = found or {"ats": "generic"}
    cache[key] = {"config": config, "checked": today, "url": url}
    return config, html, url


def today_minus(today, days):
    import datetime as dt
    return (dt.date.fromisoformat(today) - dt.timedelta(days=days)).isoformat()


def fetch(firm, cache, today):
    config, html, url = resolve(firm, cache, today)
    config = {**config, "search": firm.get("search", "")}
    if config["ats"] == "generic":
        return "generic", generic({"url": url, "browser": firm.get("browser", False)}, html)
    return config["ats"], ADAPTERS[config["ats"]](config)
