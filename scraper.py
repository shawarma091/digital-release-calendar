#!/usr/bin/env python3
"""
Scrapes https://www.dvdsreleasedates.com/digital-releases/ (and the next
few months of the same listing) and writes an .ics calendar file that
can be published somewhere public and subscribed to in iCloud Calendar.

Usage:
    python scraper.py [months_ahead] [output_path]

    months_ahead   how many months forward to also pull (default 3)
    output_path    where to write the .ics file (default docs/releases.ics)
"""
import re
import sys
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.dvdsreleasedates.com"
START_PATH = "/digital-releases/"

DATE_RE = re.compile(
    r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
    r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})"
)
MOVIE_HREF_RE = re.compile(r"^/movies/\d+/[\w-]+/?$")
FORMAT_WORDS = {"DVD", "Blu-ray", "4K"}

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; release-calendar-bot/1.0)"}


def fetch(url):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def find_next_month_url(soup):
    """Look for the '>' next-month pagination link."""
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if re.match(r"^[A-Za-z]{3}\s+\d{4}\s*>$", text) or text.endswith(">"):
            if "/releases/" in a["href"]:
                return urljoin(BASE, a["href"])
    return None


def parse_page(soup):
    """
    Walk the page in document order. Whenever we see text that looks like
    a release date, remember it. Whenever we see a link to a movie page,
    record it under the most recently seen date.
    """
    events = []
    current_date = None
    seen_hrefs_on_date = set()

    for el in soup.body.descendants:
        # Track dates from plain text nodes
        if isinstance(el, str):
            text = el.strip()
            if not text:
                continue
            m = DATE_RE.match(text)
            if m:
                try:
                    current_date = datetime.strptime(
                        f"{m.group(2)} {m.group(3)} {m.group(4)}", "%B %d %Y"
                    ).date()
                    seen_hrefs_on_date = set()
                except ValueError:
                    pass
            continue

        # Track movie links
        if getattr(el, "name", None) == "a" and el.has_attr("href"):
            href = el["href"]
            if not MOVIE_HREF_RE.match(href):
                continue
            title = el.get_text(strip=True)
            if not title or title in FORMAT_WORDS:
                continue
            title = re.sub(r"\s*DVD Release Date$", "", title).strip()
            if not title or current_date is None:
                continue
            if href in seen_hrefs_on_date:
                continue
            seen_hrefs_on_date.add(href)
            events.append(
                {
                    "title": title,
                    "date": current_date,
                    "url": urljoin(BASE, href),
                }
            )

    return events


def dedupe(events):
    seen = {}
    for e in events:
        key = (e["url"], e["date"])
        seen[key] = e
    return list(seen.values())


def make_uid(event):
    raw = f"{event['url']}|{event['date'].isoformat()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest() + "@dvdsreleasedates-calendar"


def escape_ics(text):
    return (
        text.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("\n", "\\n")
    )


def build_ics(events):
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//release-calendar-bot//dvdsreleasedates//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:New Digital/DVD Releases",
        "X-WR-TIMEZONE:UTC",
    ]
    for e in events:
        date_str = e["date"].strftime("%Y%m%d")
        next_day = (e["date"] + timedelta(days=1)).strftime("%Y%m%d")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{make_uid(e)}",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{date_str}",
            f"DTEND;VALUE=DATE:{next_day}",
            f"SUMMARY:{escape_ics(e['title'])} (Release)",
            f"DESCRIPTION:{escape_ics(e['url'])}",
            f"URL:{escape_ics(e['url'])}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    months_ahead = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    output_path = sys.argv[2] if len(sys.argv) > 2 else "docs/releases.ics"

    all_events = []
    url = urljoin(BASE, START_PATH)
    for _ in range(months_ahead + 1):
        print(f"Fetching {url}")
        soup = fetch(url)
        page_events = parse_page(soup)
        print(f"  found {len(page_events)} releases")
        all_events.extend(page_events)

        next_url = find_next_month_url(soup)
        if not next_url:
            break
        url = next_url

    all_events = dedupe(all_events)
    all_events.sort(key=lambda e: e["date"])

    ics = build_ics(all_events)

    import os
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ics)

    print(f"Wrote {len(all_events)} events to {output_path}")


if __name__ == "__main__":
    main()
