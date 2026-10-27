#!/usr/bin/env python3
"""Fetch publicly available RSS headlines for Aurora Digital Edition.

Uses Python's standard library only. No scraping, paid API, or generated news.
"""
import datetime as dt
import email.utils
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "news.json"
UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)
FEEDS = [
    ("NRK Oslo", "lokalt", "lokalt", "https://www.nrk.no/ostlandssendingen/siste.rss", False),
    ("NRK Norge", "norge", "norge", "https://www.nrk.no/norge/toppsaker.rss", False),
    ("NRK Siste", "norge", "norge", "https://www.nrk.no/nyheter/siste.rss", False),
    ("NRK Sport", "sport", "sport", "https://www.nrk.no/sport/siste.rss", False),
    ("NRK Kultur", "kultur", "kultur", "https://www.nrk.no/kultur/toppsaker.rss", False),
    ("NRK Vestfold", "holmestrand", "lokalt", "https://www.nrk.no/vestfoldogtelemark/siste.rss", True),
    ("Jarlsberg Avis", "holmestrand", "lokalt", "https://www.jarlsbergavis.no/service/rss", False),
    ("Sande Avis", "holmestrand", "lokalt", "https://www.sandeavis.no/service/rss", False),
    ("BBC World", "verden", "verden", "https://feeds.bbci.co.uk/news/world/rss.xml", False),
    ("BBC Technology", "teknologi", "teknologi", "https://feeds.bbci.co.uk/news/technology/rss.xml", False),
    ("BBC Business", "okonomi", "okonomi", "https://feeds.bbci.co.uk/news/business/rss.xml", False),
]
HOLMESTRAND = re.compile(r"holmestrand|sande(?:\s+i\s+vestfold)?|\bhof\b|selvik|eidsfoss|sundbyfoss", re.I)
TAG = re.compile(r"<[^>]*>")
SPACE = re.compile(r"\s+")
HEADERS = {"User-Agent": "AuroraDigitalEditionRSS/1.0 (public RSS reader)", "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"}

def plain(value, limit=320):
    return SPACE.sub(" ", html.unescape(TAG.sub(" ", value or ""))).strip()[:limit]

def findtext(node, tags):
    for tag in tags:
        value = node.findtext(tag)
        if value and value.strip():
            return value.strip()
    return ""

def when(raw):
    if not raw:
        return None
    try:
        value = email.utils.parsedate_to_datetime(raw)
        if value:
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    except (ValueError, TypeError, IndexError):
        pass
    try:
        value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    except ValueError:
        return None

def safe_url(raw):
    try:
        u = urllib.parse.urlsplit(raw.strip())
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            return ""
        if not (u.hostname == "nrk.no" or u.hostname.endswith(".nrk.no") or u.hostname == "bbc.co.uk" or u.hostname.endswith(".bbc.co.uk") or u.hostname == "bbc.com" or u.hostname.endswith(".bbc.com") or u.hostname in ("jarlsbergavis.no", "www.jarlsbergavis.no", "sandeavis.no", "www.sandeavis.no")):
            return ""
        query = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
        query = [(k,v) for k,v in query if not k.lower().startswith("utm_")]
        return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(query), ""))
    except ValueError:
        return ""

def read_feed(source, category, topic, url, local_only):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=18) as response:
        body = response.read(1_500_000)
    root = ET.fromstring(body)
    rss = root.findall(".//item")
    atom = root.findall("{http://www.w3.org/2005/Atom}entry")
    nodes = rss or atom
    found = []
    for node in nodes[:70]:
        title = plain(findtext(node, ["title", "{http://www.w3.org/2005/Atom}title"]), 220)
        desc = plain(findtext(node, ["description", "summary", "{http://www.w3.org/2005/Atom}summary", "{http://www.w3.org/2005/Atom}content"]), 260)
        link = findtext(node, ["link"])
        if not link:
            for element in node.findall("{http://www.w3.org/2005/Atom}link"):
                if element.get("rel") in (None, "alternate") and element.get("href"):
                    link = element.get("href")
                    break
        link = safe_url(link)
        if not title or not link or (local_only and not HOLMESTRAND.search(title + " " + desc)):
            continue
        published = when(findtext(node, ["pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated", "{http://purl.org/dc/elements/1.1/}date"]))
        if not published or published > NOW + dt.timedelta(hours=2) or published < NOW - dt.timedelta(days=10):
            continue
        found.append({
            "source": source, "category": category, "topic": topic,
            "title": title, "url": link, "summary": desc,
            "date_label": published.astimezone(dt.timezone(dt.timedelta(hours=1))).strftime("%d.%m.%Y"),
            "timestamp": int(published.timestamp())
        })
    return found

def main():
    articles = []
    ok = 0
    for entry in FEEDS:
        try:
            results = read_feed(*entry)
            ok += 1
            articles.extend(results)
            print(f"{entry[0]}: {len(results)} saker")
        except Exception as exc:
            print(f"ADVARSEL: {entry[0]}: {exc}", file=sys.stderr)
    if ok < 2:
        raise SystemExit("Færre enn to RSS-kilder tilgjengelige. Beholder forrige fil.")
    # Preserve previously published articles during temporary source outages.
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
        articles.extend(old.get("items", []))
    except (OSError, ValueError, TypeError):
        pass
    dedup = {}
    for article in articles:
        url = article.get("url", "")
        stamp = int(article.get("timestamp") or 0)
        if url and stamp >= int((NOW - dt.timedelta(days=10)).timestamp()):
            if url not in dedup or stamp > int(dedup[url].get("timestamp") or 0):
                dedup[url] = article
    ordered = sorted(dedup.values(), key=lambda x: int(x.get("timestamp") or 0), reverse=True)[:400]
    if not ordered:
        raise SystemExit("Ingen gyldige nyheter; beholder forrige fil.")
    payload = {"updated": NOW.isoformat(timespec="seconds").replace("+00:00", "Z"),
               "source_count": ok, "items": ordered}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Skrev {len(ordered)} artikler fra {ok} RSS-kilder.")

if __name__ == "__main__":
    main()
