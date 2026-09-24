"""Daily run: fetch every firm, keep relevant roles, update history, build the site.

Usage:
    python -m jobwatch.run                 # full run
    python -m jobwatch.run --firm Heitman  # test one firm, writes nothing
    python -m jobwatch.run --detect        # show which job system each firm uses
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import smtplib
import sys
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

import yaml

from . import contacts, dates, dedupe, linkedin, sources

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"


def load_yaml():
    return yaml.safe_load((ROOT / "firms.yaml").read_text())


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def compile_words(words):
    """Short words must match whole; longer ones match at the start of a word."""
    parts = []
    for w in words:
        w = re.escape(w.lower())
        parts.append(rf"\b{w}\b" if len(w) <= 3 else rf"\b{w}")
    return re.compile("|".join(parts), re.I) if parts else None


class Filter:
    def __init__(self, cfg):
        self.roles = compile_words(cfg["role_keywords"])
        self.exclude = compile_words(cfg["exclude_keywords"])
        self.sector = compile_words(cfg["sector_keywords"])
        self.locations = compile_words(cfg["locations"])
        self.am = compile_words(cfg["tracks"]["asset_management"])
        self.inv = compile_words(cfg["tracks"]["investments"])
        self.europe = compile_words(cfg.get("europe_keywords") or [])
        self.abroad = compile_words(cfg.get("exclude_places") or [])
        self.europe_only = bool(cfg.get("europe_only"))

    def is_europe(self, job):
        text = f"{job['title']} {job.get('department', '')}"
        return bool(self.europe and self.europe.search(text))

    def keep(self, job, firm):
        title = job["title"]
        if not title or (self.exclude and self.exclude.search(title)):
            return False
        if not firm.get("trust_titles") and not self.roles.search(title):
            return False
        if firm.get("broad"):
            if not self.sector.search(f"{title} {job.get('department', '')}"):
                return False
        if self.europe_only and not self.is_europe(job):
            return False
        where = f"{title} {job.get('location', '')}"
        if self.abroad and self.abroad.search(where) and not self.locations.search(where):
            return False
        loc = job.get("location", "")
        if loc and self.locations and not self.locations.search(loc):
            if not re.match(r"\d+ locations", loc, re.I):
                return False
        return True

    def track(self, title):
        if self.am.search(title):
            return "Asset management"
        if self.inv.search(title):
            return "Investments"
        return "Other"


def fetch_one(firm, cache, today):
    try:
        adapter, jobs = sources.fetch(firm, cache, today)
        return firm, adapter, jobs, None
    except Exception as e:  # one broken site must not stop the run
        return firm, None, [], f"{type(e).__name__}: {e}"[:300]


def fetch_all(firms, cache, today):
    plain = [f for f in firms if not f.get("browser")]
    browser = [f for f in firms if f.get("browser")]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda f: fetch_one(f, cache, today), plain))
    results += [fetch_one(f, cache, today) for f in browser]
    return results


class Tracker:
    """Adds sightings to the history so each real role appears once.

    A role is identified by firm plus a cleaned title, not by URL. So a new
    tracking link, a repost, the same advert in several locations, or the same
    role found on LinkedIn all land on the one existing entry.
    """

    def __init__(self, history, flt, today):
        self.history, self.flt, self.today = history, flt, today
        self.new, self.hits = [], {}

    def add(self, job, firm, source, kind):
        jid = dedupe.role_id(firm, job["title"])
        url = dedupe.canonical_url(job["url"])
        rec = self.history.get(jid)
        hits = self.hits.setdefault(jid, set())
        if rec is None:
            rec = {**job, "url": url, "id": jid, "firm": firm, "source": source,
                   "kind": kind, "first_seen": self.today, "links": [], "postings": 1,
                   "track": self.flt.track(job["title"])}
            self.history[jid] = rec
            self.new.append(rec)
        else:
            links = rec.setdefault("links", [])
            first_today = source not in hits
            if kind == "firm" and rec["kind"] != "firm":
                # A direct listing becomes the main link; keep the other as extra.
                links.append({"source": rec["source"], "url": rec["url"]})
                rec.update(url=url, source=source, kind=kind, postings=1,
                           location=job["location"] or rec.get("location", ""))
            elif source == rec["source"]:
                if first_today:
                    rec.update(url=url, postings=1)  # keep the latest link
                elif url != rec["url"]:
                    rec["postings"] = rec.get("postings", 1) + 1  # another location
            else:
                other = [l for l in links if l["source"] == source]
                if other:
                    other[0]["url"] = url
                else:
                    links.append({"source": source, "url": url})
            if not rec.get("active"):
                rec["reposted"] = self.today
        hits.add(source)
        set_posted(rec, job.get("posted"), self.today)
        rec.update(last_seen=self.today, active=True, closed=None)

    def close_missing(self, ok_sources):
        li_cutoff = (dt.date.fromisoformat(self.today) - dt.timedelta(days=30)).isoformat()
        for rec in self.history.values():
            if not rec.get("active"):
                continue
            if rec["kind"] == "linkedin":
                if rec["last_seen"] < li_cutoff:
                    rec.update(active=False, closed=self.today)
            elif rec["source"] in ok_sources and rec["last_seen"] != self.today:
                rec.update(active=False, closed=self.today)

    def flag_recruiter_matches(self):
        direct = [r for r in self.history.values() if r["kind"] == "firm" and r.get("active")]
        for rec in self.history.values():
            rec.pop("maybe_same", None)
            if rec["kind"] == "firm" or not rec.get("active"):
                continue
            best = max(direct, key=lambda d: dedupe.similar(rec["title"], d["title"]), default=None)
            if best and dedupe.similar(rec["title"], best["title"]) >= 0.8:
                rec["maybe_same"] = {"id": best["id"], "firm": best["firm"], "title": best["title"]}


def set_posted(rec, value, today):
    """Keep the earliest posting date any source reports for this role."""
    iso, approx = dates.parse_posted(value, today)
    if iso and (not rec.get("posted_on") or iso < rec["posted_on"]):
        rec["posted_on"], rec["posted_approx"] = iso, approx


def add_contacts(history, limit, today):
    """Open each new advert once for contact details and its posting date."""
    todo = [r for r in history.values()
            if r.get("active") and not r.get("details_checked") and r["kind"] != "linkedin"][:limit]

    def one(rec):
        try:
            found = contacts.find(rec["url"])
        except Exception:
            found = {}
        set_posted(rec, found.pop("posted", ""), today)
        where = found.pop("location", "")
        if where and (not rec.get("location") or re.match(r"\d+ locations", rec["location"], re.I)):
            rec["location"] = where
        rec["contact"] = found or rec.get("contact") or {}
        rec["details_checked"] = True

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(one, todo))


def add_linkedin(tracker, status, flt, li_cfg, user, pw, known):
    try:
        jobs, err = linkedin.fetch(user, pw, li_cfg.get("days", 3)), None
    except Exception as e:
        jobs, err = [], f"{type(e).__name__}: {e}"[:300]
    kept = 0
    for j in jobs:
        rec = {"title": j["title"], "url": j["url"], "location": j["location"],
               "posted": "", "department": ""}
        if not flt.keep(rec, {"trust_titles": True}):
            continue
        kept += 1
        firm = dedupe.match_firm(j["company"], known) or "Unknown company"
        tracker.add(rec, firm, "LinkedIn alerts", "linkedin")
    status.append({"firm": "LinkedIn alerts", "adapter": "gmail", "fetched": len(jobs),
                   "matched": kept, "error": err,
                   "careers_url": "https://www.linkedin.com/jobs/"})
    return err is None


def via(j):
    kind = j.get("kind", "firm")
    if kind == "recruiter":
        return " (recruiter)"
    if kind == "linkedin":
        return ", via LinkedIn"
    return ""


def send_digest(new_jobs, site_url):
    user, pw, to = (os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASS", "DIGEST_TO"))
    if not (user and pw and to and new_jobs):
        return
    rows = []
    for j in sorted(new_jobs, key=lambda j: (j["track"] != "Asset management", j["firm"])):
        c = j.get("contact") or {}
        contact = ", ".join(x for x in [c.get("name"), c.get("email"), c.get("phone")] if x)
        extra = f"<br>Contact: {escape(contact)}" if contact else ""
        same = j.get("maybe_same")
        if same:
            extra += (f"<br>Possibly the same role as {escape(same['title'])} "
                      f"at {escape(same['firm'])}")
        if j.get("posted_on"):
            extra += f"<br>Posted {'before ' if j.get('posted_approx') else ''}{j['posted_on']}"
        rows.append(f'<p><a href="{escape(j["url"])}">{escape(j["title"])}</a><br>'
                    f'{escape(j["firm"])}, {escape(j["location"] or "location not listed")}'
                    f'{via(j)}{extra}</p>')
    link = f'<p><a href="{escape(site_url)}">Open the full list</a></p>' if site_url else ""
    msg = MIMEText(f"<html><body>{''.join(rows)}{link}</body></html>", "html")
    msg["Subject"] = f"{len(new_jobs)} new real estate roles"
    msg["From"], msg["To"] = user, to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pw)
        s.send_message(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--firm", help="run a single firm or recruiter by name and print results")
    ap.add_argument("--detect", action="store_true", help="print detected job systems")
    args = ap.parse_args()

    cfg = load_yaml()
    firms = cfg["firms"] + [dict(r, kind="recruiter", broad=r.get("broad", False))
                            for r in cfg.get("recruiters") or []]
    flt = Filter(cfg["filters"])
    today = dt.date.today().isoformat()
    cache = load_json(DATA / "ats_cache.json", {})

    if args.detect:
        for f in firms:
            try:
                conf = sources.resolve(f, {}, today)[0]
                print(f"{f['name']:<32} {conf}")
            except Exception as e:
                print(f"{f['name']:<32} ERROR {e}")
        return

    if args.firm:
        sel = [f for f in firms if f["name"].lower() == args.firm.lower()]
        if not sel:
            sys.exit(f"No firm or recruiter named {args.firm} in firms.yaml")
        firm, adapter, jobs, err = fetch_one(sel[0], {}, today)
        print(f"adapter: {adapter}  fetched: {len(jobs)}  error: {err}")
        for j in jobs:
            mark = "KEEP" if flt.keep(j, firm) else "    "
            print(f"{mark}  {j['title']}  [{j['location']}]  {j['url']}")
        return

    history = load_json(DATA / "jobs.json", {})
    tracker = Tracker(history, flt, today)
    status, ok_sources = [], set()

    # Firms first, so a direct listing is the main entry for any shared role.
    results = fetch_all(firms, cache, today)
    results.sort(key=lambda r: r[0].get("kind", "firm") != "firm")
    for firm, adapter, jobs, err in results:
        name = firm["name"]
        kept = [j for j in jobs if flt.keep(j, firm)]
        status.append({"firm": name, "adapter": adapter, "fetched": len(jobs),
                       "matched": len(kept), "error": err,
                       "careers_url": firm["careers_url"]})
        if err:
            continue  # leave this source's roles untouched on failure
        ok_sources.add(name)
        for j in kept:
            tracker.add(j, name, name, firm.get("kind", "firm"))

    li_cfg = cfg.get("linkedin_alerts") or {}
    user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    if li_cfg.get("enabled") and user and pw:
        add_linkedin(tracker, status, flt, li_cfg, user, pw, [f["name"] for f in firms])

    tracker.close_missing(ok_sources)
    tracker.flag_recruiter_matches()
    c_cfg = cfg.get("contacts") or {}
    if c_cfg.get("enabled", True):
        add_contacts(history, c_cfg.get("max_per_run", 80), today)

    # Re-check every open role against today's filters, so tightened settings
    # and locations found on the advert page also clear out earlier entries.
    by_name = {f["name"]: f for f in firms}
    for jid in list(history):
        rec = history[jid]
        if not rec.get("active"):
            continue
        fcfg = {"trust_titles": True} if rec["kind"] == "linkedin" else by_name.get(rec["source"], {})
        if not flt.keep(rec, fcfg):
            del history[jid]

    # Drop roles closed more than 60 days ago to keep the file small.
    cutoff = (dt.date.today() - dt.timedelta(days=60)).isoformat()
    history = {k: v for k, v in history.items()
               if v.get("active") or (v.get("closed") or today) >= cutoff}
    for rec in history.values():
        rec["europe"] = flt.is_europe(rec)

    DATA.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)
    (DATA / "jobs.json").write_text(json.dumps(history, indent=1, sort_keys=True))
    (DATA / "ats_cache.json").write_text(json.dumps(cache, indent=1, sort_keys=True))

    public = sorted(history.values(), key=lambda j: (j["first_seen"], j["firm"]), reverse=True)
    (DOCS / "jobs.json").write_text(json.dumps(
        {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
         "jobs": public}))
    (DOCS / "status.json").write_text(json.dumps(sorted(status, key=lambda s: s["firm"])))
    shutil.copy(ROOT / "site" / "index.html", DOCS / "index.html")
    (DOCS / ".nojekyll").touch()

    failed = [s["firm"] for s in status if s["error"]]
    print(f"{len(tracker.new)} new, {sum(1 for j in public if j.get('active'))} open, "
          f"{len(failed)} sources failed: {', '.join(failed) or 'none'}")
    send_digest(tracker.new, os.environ.get("SITE_URL", ""))


if __name__ == "__main__":
    main()
