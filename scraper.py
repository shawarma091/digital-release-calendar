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

# Headings that mark the end of the actual release listing and the start
# of sidebar/footer content (e.g. "Most Requested DVD Release Dates").
# Once any of these is seen, we stop scraping — everything after this
# point is not a real release-date entry.
STOP_HEADINGS = [
    "Most Requested DVD Release Dates",
    "DVDs by Genre",
    "New Movies by Year",
]

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
    Walk every tag in document order. For each tag, check whether its
    FULL visible text (get_text, which safely bridges any nested spans
    or comment nodes) starts with a release-date pattern, and if so
    update the "current date". Whenever we see a link to a movie page,
    record it under the most recently seen date.

    Checking get_text() on every tag (rather than raw text nodes) means
    we don't get fooled by a date being split across sibling tags/inline
    comment markers, which is what caused every movie to collapse onto
    a single date previously.
    """
    events = []
    current_date = None
    seen_hrefs = set()

    for tag in soup.body.find_all(True):
        # Update current_date if this tag's visible text starts with a date
        own_text = tag.get_text(" ", strip=True)

        # Stop entirely once we hit the sidebar/footer content — the
        # length check avoids false-matching a big ancestor container
        # whose text merely CONTAINS one of these phrases somewhere deep
        # inside it, rather than being the heading itself.
        if own_text and len(own_text) < 100:
            if any(own_text.startswith(h) for h in STOP_HEADINGS):
                break

        if own_text:
            m = DATE_RE.match(own_text)
            if m:
                try:
                    parsed = datetime.strptime(
                        f"{m.group(2)} {m.group(3)} {m.group(4)}", "%B %d %Y"
                    ).date()
                    current_date = parsed
                except ValueError:
                    pass

        # Track movie links
        if tag.name == "a" and tag.has_attr("href"):
            href = tag["href"]
            if not MOVIE_HREF_RE.match(href):
                continue
            title = tag.get_text(strip=True)
            if not title or title in FORMAT_WORDS:
                continue
            title = re.sub(r"\s*DVD Release Date$", "", title).strip()
            if not title or current_date is None:
                continue
            key = (href, current_date)
            if key in seen_hrefs:
                continue
            seen_hrefs.add(key)
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

    # Debug summary: how many events landed on each date, so a bad
    # scrape (e.g. everything collapsing onto one date) is obvious
    # from the Action log without needing to inspect the .ics file.
    from collections import Counter
    counts = Counter(e["date"] for e in all_events)
    print("Events per date:")
    for d in sorted(counts):
        print(f"  {d}: {counts[d]}")

    ics = build_ics(all_events)

    import os
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ics)

    print(f"Wrote {len(all_events)} events to {output_path}")


if __name__ == "__main__":
    main()
