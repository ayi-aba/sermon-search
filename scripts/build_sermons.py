#!/usr/bin/env python3
"""Build sermons.json from the Coda Sermon Recordings table and the Podbean RSS feed.

Standard library only, so the GitHub Action needs no pip install.

  CODA_API_TOKEN=... python3 scripts/build_sermons.py
  python3 scripts/build_sermons.py --rows-file rows.json   # offline, rows already exported

The Podbean feed only carries the latest ~50 episodes, so every episode seen is kept in
data/podbean-cache.json; descriptions stay searchable after an episode leaves the feed.
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "sermons.json")
CACHE_PATH = os.path.join(ROOT, "data", "podbean-cache.json")

CODA_API = os.environ.get("CODA_API_BASE", "https://coda.io/apis/v1")
DOC_ID = "5EeoPIgwLq"
TABLE_ID = "grid-u2I-3m_av-"
# Column IDs, not names, so renames in Coda don't break the sync.
COL_EVENT = "c-uoY8gR621F"
COL_START = "c-qU8rllqqaY"
COL_TITLE = "c-rztzDkTd3c"
COL_REFS = "c-F2WNyOHuMB"
COL_LINK = "c-Ek32zPL3YX"

FEED_URL = "https://feed.podbean.com/alabastergroup/feed.xml"
USER_AGENT = "alabaster-sermon-search/1.0 (+https://github.com/ayi-aba)"

# A run that sees far fewer rows than this is treated as a broken read, not as deletions.
MIN_ROWS = 200

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


# ---------- Coda ----------

def cell_text(value):
    """Flatten a Coda cell (simpleWithArrays or rich format) to plain text."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("url") or value.get("name") or value.get("value") or "")
    if isinstance(value, list):
        return ", ".join(cell_text(v) for v in value)
    text = str(value).strip()
    md_link = re.fullmatch(r"\[([^\]]*)\]\((\S+)\)", text)
    return md_link.group(2) if md_link else text


def cell_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [cell_text(v) for v in value if cell_text(v)]
    return [s for s in (x.strip() for x in cell_text(value).split(",")) if s]


def parse_start(start, event, link):
    """Start comes back as display text ("Sun, May 7, 10:30 AM", no year), so the year
    comes from the event name, the Podbean slug, or failing those the weekday."""
    if re.match(r"\d{4}-\d{2}-\d{2}", start):
        return start[:10]
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", event)
    if m:
        return date(int(m[3]), int(m[1]), int(m[2])).isoformat()
    m = re.match(r"(\w{3}), (\w{3}) (\d{1,2})", start)
    if not m:
        return ""
    dow, month, day = m[1], MONTHS.index(next(x for x in MONTHS if x.startswith(m[2]))) + 1, int(m[3])
    hint = re.search(r"\b20(\d\d)\b|HMW (\d\d)\b", event) or re.search(r"/e/(\d\d)\d{4}-", link)
    years = range(2020, date.today().year + 2)
    fits = [y for y in years if date(y, month, day).strftime("%a") == dow]
    if hint:
        year = 2000 + int(next(g for g in hint.groups() if g))
        if year in fits or not fits:
            return date(year, month, day).isoformat()
    past = [y for y in fits if date(y, month, day) <= date.today()]
    return date((past or fits)[-1], month, day).isoformat() if fits else ""


def fetch_coda_rows(token):
    rows, page_token = [], None
    while True:
        params = {"valueFormat": "simpleWithArrays", "limit": 200}
        if page_token:
            params["pageToken"] = page_token
        url = f"{CODA_API}/docs/{DOC_ID}/tables/{TABLE_ID}/rows?{urllib.parse.urlencode(params)}"
        data = json.loads(http_get(url, {"Authorization": f"Bearer {token}"}))
        for item in data["items"]:
            v = item["values"]
            event, link = cell_text(v.get(COL_EVENT)), cell_text(v.get(COL_LINK))
            rows.append({
                "date": parse_start(cell_text(v.get(COL_START)), event, link),
                "event": event,
                "title": cell_text(v.get(COL_TITLE)),
                "refs": cell_list(v.get(COL_REFS)),
                "link": link,
            })
        page_token = data.get("nextPageToken")
        if not page_token:
            return rows


# ---------- Podbean ----------

def clean_html(text):
    text = re.sub(r"<(br|/p|/div|/li)[^>]*>", "\n", text or "", flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = (re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def fetch_feed():
    root = ET.fromstring(http_get(FEED_URL))
    episodes = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        m = re.match(r"(\d{2})(\d{2})(\d{2})\s+(.*)", title)
        episodes.append({
            "link": (item.findtext("link") or "").strip(),
            "title": m.group(4) if m else title,
            "date": f"20{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else "",
            "description": clean_html(item.findtext("description")),
        })
    return episodes


def norm_url(url):
    u = urllib.parse.urlsplit(url.strip().lower())
    host = u.netloc.removeprefix("www.")
    return f"{host}{u.path.rstrip('/')}" if host else ""


def link_kind(url):
    if not url:
        return "none"
    if "sharepoint.com" in url:
        return "sharepoint"
    if "podbean.com/ew/" in url:
        return "embed"
    if urllib.parse.urlsplit(url).path.lower().endswith(".mp3"):
        return "mp3"
    return "page"


# ---------- References ----------

def title_word(w):
    return w if w.lower() == "of" else w[:1].upper() + w[1:].lower()


def normalize_refs(raw_refs):
    """'acts 5' -> 'Acts 5'; 'Matthew 13, 16, 17' -> three refs; 'Proverbs 17:2' -> 'Proverbs 17'."""
    out = []
    for raw in raw_refs:
        book = None
        for part in re.split(r"[,;]", raw):
            part = re.sub(r"\s+", " ", part).strip()
            if not part:
                continue
            m = re.fullmatch(r"((?:[1-3] )?[A-Za-z]+(?: of [A-Za-z]+)?)(?: (\d+(?:-\d+)?)(?::[\d\-–,]+)?)?", part)
            if m:
                book = " ".join(title_word(w) for w in m.group(1).split())
                ref = f"{book} {m.group(2)}" if m.group(2) else book
            elif book and re.fullmatch(r"\d+(-\d+)?(:[\d\-–]+)?", part):
                ref = f"{book} {part.split(':')[0]}"  # bare chapter continuing "Matthew 13, 16"
            else:
                ref = part
            if ref not in out:
                out.append(ref)
    return out


def date_spellings(iso):
    d = date.fromisoformat(iso)
    month = MONTHS[d.month - 1]
    return [
        iso,
        f"{d.month}/{d.day}/{d.year}",
        f"{d.month:02d}/{d.day:02d}/{d.year}",
        f"{month} {d.day}, {d.year}",
        f"{month[:3]} {d.day}, {d.year}",
        f"{month} {d.year}",
        f"{month[:3]} {d.year}",
    ]


# ---------- Merge ----------

def build(rows, cache):
    by_url = {norm_url(ep["link"]): ep for ep in cache.values()}
    by_date = {}
    for ep in cache.values():
        if ep["date"]:
            by_date.setdefault(ep["date"], []).append(ep)
    rows_per_date = {}
    for r in rows:
        rows_per_date[r["date"]] = rows_per_date.get(r["date"], 0) + 1

    sermons, skipped = [], {"sharepoint": 0, "untitled": 0, "undated": 0}
    for r in rows:
        if not r["date"]:
            skipped["undated"] += 1
            continue
        link, kind = r["link"].strip(), link_kind(r["link"])
        ep = by_url.get(norm_url(link)) if link else None
        # Date match only when it's unambiguous on both sides (retreat days have several sessions).
        if not ep and len(by_date.get(r["date"], [])) == 1 and rows_per_date[r["date"]] == 1:
            ep = by_date[r["date"]][0]
        if ep and kind in ("none", "sharepoint"):
            link, kind = ep["link"], "page"
        if kind == "sharepoint":
            skipped["sharepoint"] += 1  # plenary recordings are not public
            continue
        title = r["title"].strip() or (ep["title"] if ep else "")
        if not title:
            skipped["untitled"] += 1
            continue

        refs = normalize_refs(r["refs"])
        description = ep["description"] if ep else ""
        event = r["event"].strip()
        search_parts = [title, *refs, event, description, *date_spellings(r["date"])]
        sermons.append({
            "date": r["date"],
            "title": title,
            "refs": refs,
            "event": event,
            "link": link or None,
            "public": True,
            "description": description,
            "searchText": " | ".join(p for p in search_parts if p).lower(),
        })

    sermons.sort(key=lambda s: (s["date"], s["title"]), reverse=True)
    return sermons, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows-file", help="JSON list of {date, event, title, refs, link} instead of the Coda API")
    ap.add_argument("--no-feed", action="store_true", help="use only data/podbean-cache.json")
    args = ap.parse_args()

    if args.rows_file:
        with open(args.rows_file) as f:
            rows = json.load(f)
    else:
        token = os.environ.get("CODA_API_TOKEN")
        if not token:
            sys.exit("CODA_API_TOKEN is not set")
        rows = fetch_coda_rows(token)
    if len(rows) < MIN_ROWS:
        sys.exit(f"Only {len(rows)} Coda rows read (expected {MIN_ROWS}+); keeping the last good sermons.json")

    cache = {}
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            cache = json.load(f)
    if not args.no_feed:
        for ep in fetch_feed():
            if ep["link"]:
                cache[norm_url(ep["link"])] = ep

    sermons, skipped = build(rows, cache)

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(dict(sorted(cache.items(), reverse=True)), f, indent=1, ensure_ascii=False)
        f.write("\n")
    with open(OUT_PATH, "w") as f:
        json.dump(sermons, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")

    with_desc = sum(1 for s in sermons if s["description"])
    print(f"{len(rows)} Coda rows -> {len(sermons)} sermons "
          f"({with_desc} with descriptions; left out: {skipped['sharepoint']} SharePoint-only, "
          f"{skipped['untitled']} untitled, {skipped['undated']} undated); {os.path.getsize(OUT_PATH) // 1024} KB")


if __name__ == "__main__":
    main()
