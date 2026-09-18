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
import calendar
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
    "Follow DVDs Release Dates",
    "Most Requested DVD Release Dates",
    "DVDs by Genre",
    "New Movies by Year",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; release-calendar-bot/1.0)"}


def fetch(url):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def next_month_url(year, month):
    """
    Build the following month's DIGITAL releases URL.

    Pattern:
    /digital-releases/{year}/{month}/digital-hd-releases-{monthname}-{year}

    This must not use /releases/... because that is the DVD/Blu-ray calendar.
    """
    month += 1
    if month > 12:
        month = 1
        year += 1
    month_name = calendar.month_name[month].lower()
    return (
        f"{BASE}/digital-releases/{year}/{month}/"
        f"digital-hd-releases-{month_name}-{year}"
    )


def parse_page(soup):
    """
    Parse only the real release listing.

    First locate the sidebar/footer heading in the DOM and create a hard
    positional cutoff. This prevents "Most Requested" movie links from
    inheriting the final genuine release date.
    """
    events = []
    current_date = None
    seen_hrefs = set()

    body = soup.body
    if body is None:
        print("  WARNING: page has no <body>")
        return events

    tags = list(body.find_all(True))
    tag_position = {id(tag): i for i, tag in enumerate(tags)}

    stop_tag = None
    stop_heading = None

    # Preferred method: find an actual sidebar heading.
    for tag in body.find_all(re.compile(r"^h[1-6]$")):
        heading_text = " ".join(tag.stripped_strings)
        for heading in STOP_HEADINGS:
            if heading.lower() in heading_text.lower():
                stop_tag = tag
                stop_heading = heading_text
                break
        if stop_tag is not None:
            break

    # Fallback if the site changes the heading to another element.
    if stop_tag is None:
        for tag in tags:
            visible = " ".join(tag.stripped_strings)
            if not visible or len(visible) > 100:
                continue
            normalized = re.sub(r"\s+", " ", visible).strip().lower()
            for heading in STOP_HEADINGS:
                h = heading.lower()
                if normalized == h or normalized.startswith(h + " "):
                    stop_tag = tag
                    stop_heading = visible
                    break
            if stop_tag is not None:
                break

    cutoff = tag_position.get(id(stop_tag), len(tags))

    if stop_tag is not None:
        print(
            f"  hard DOM cutoff before sidebar content "
            f"(matched: {stop_heading!r}, tag: <{stop_tag.name}>)"
        )
    else:
        print("  WARNING: sidebar cutoff heading not found")

    for pos, tag in enumerate(tags):
        if pos >= cutoff:
            break

        own_text = tag.get_text(" ", strip=True)

        if own_text:
            m = DATE_RE.match(own_text)
            if m:
                try:
                    current_date = datetime.strptime(
                        f"{m.group(2)} {m.group(3)} {m.group(4)}",
                        "%B %d %Y",
                    ).date()
                except ValueError:
                    pass

        if tag.name != "a" or not tag.has_attr("href"):
            continue

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
    year, month = None, None

    for _ in range(months_ahead + 1):
        print(f"Fetching {url}")
        soup = fetch(url)
        page_events = parse_page(soup)
        print(f"  found {len(page_events)} releases")
        all_events.extend(page_events)

        if page_events:
            # Figure out which (year, month) this page was actually
            # showing, based on the most common month among the dates
            # we just found on it — needed so we can build the URL for
            # the following month.
            from collections import Counter
            ym_counts = Counter((e["date"].year, e["date"].month) for e in page_events)
            year, month = ym_counts.most_common(1)[0][0]
        elif year is None:
            # First page had no events at all — fall back to today's
            # month so we can still attempt to move forward.
            today = datetime.utcnow().date()
            year, month = today.year, today.month

        url = next_month_url(year, month)

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
