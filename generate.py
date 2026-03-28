#!/usr/bin/env python3
"""
Daily News Briefing Generator
Fetches RSS/Atom feeds and builds a mobile-optimised light-mode index.html.
No third-party dependencies — stdlib only.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import urllib.request
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

# ── Config ────────────────────────────────────────────────────────────────────
LOOKBACK_DAYS = 7
MAX_PER_SECTION = 6

FEEDS = {
    "world": [
        ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ],
    "tech": [
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("Hacker News", "https://news.ycombinator.com/rss"),
    ],
    "business": [
        ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("TechCrunch", "https://techcrunch.com/feed/"),
    ],
    "ghana": [
        ("JoyOnline", "https://www.myjoyonline.com/feed/"),
    ],
    "ghana_health": [
        ("JoyOnline Health", "https://www.myjoyonline.com/category/health/feed/"),
    ],
}

SECTION_META = {
    "world":        {"icon": "🌍", "title": "World News"},
    "tech":         {"icon": "💻", "title": "Tech"},
    "business":     {"icon": "📈", "title": "Business & Economy"},
    "ghana":        {"icon": "🇬🇭", "title": "Ghana"},
    "ghana_health": {"icon": "🏥", "title": "Ghana Health"},
}

# Keywords that mark a Ghana story as political (lower-priority)
GHANA_POLITICS_WORDS = [
    "ndc", "npp", "sole-sourc", "sole source", "big push",
    " mp ", " mps ", "parliament", "constituency",
    "akufo-addo", "bawumia", "mahama",
]

MEDIA_NS = "http://search.yahoo.com/mrss/"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
ATOM_NS = "http://www.w3.org/2005/Atom"


# ── Helpers ───────────────────────────────────────────────────────────────────
def fetch(url: str) -> str | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "NewsBriefing/1.0 (+github)"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [warn] {url}: {e}", file=sys.stderr)
        return None


def parse_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def strip_tags(text: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


def truncate(text: str, n: int = 220) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def first_img(text: str) -> str:
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', text or "")
    return m.group(1) if m else ""


# ── Feed parser ───────────────────────────────────────────────────────────────
def parse_feed(xml_text: str, source: str, cutoff: datetime) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  [warn] XML error ({source}): {e}", file=sys.stderr)
        return []

    is_atom = ATOM_NS in root.tag
    entries = (
        root.findall(f"{{{ATOM_NS}}}entry")
        if is_atom
        else root.findall(".//item")
    )

    def get(el, *tags):
        for tag in tags:
            child = el.find(tag)
            if child is not None and child.text:
                return child.text.strip()
        return ""

    items = []
    for e in entries:
        # Title
        title = strip_tags(
            get(e, f"{{{ATOM_NS}}}title") if is_atom else get(e, "title")
        )
        if not title:
            continue

        # Link
        if is_atom:
            link_el = e.find(f"{{{ATOM_NS}}}link")
            link = link_el.get("href", "") if link_el is not None else ""
        else:
            link = get(e, "link")

        # Date
        date_obj = None
        if is_atom:
            for dtag in [f"{{{ATOM_NS}}}updated", f"{{{ATOM_NS}}}published"]:
                el = e.find(dtag)
                if el is not None and el.text:
                    date_obj = parse_date(el.text.strip())
                    if date_obj:
                        break
        else:
            date_obj = parse_date(get(e, "pubDate"))

        if date_obj and date_obj < cutoff:
            continue

        # Description
        desc = ""
        if is_atom:
            for stag in [f"{{{ATOM_NS}}}summary", f"{{{ATOM_NS}}}content"]:
                el = e.find(stag)
                if el is not None and el.text:
                    desc = truncate(strip_tags(el.text))
                    break
        else:
            raw_desc = get(e, "description")
            if raw_desc:
                desc = truncate(strip_tags(raw_desc))
            if not desc:
                el = e.find(f"{{{CONTENT_NS}}}encoded")
                if el is not None and el.text:
                    desc = truncate(strip_tags(el.text))

        # Image
        img = ""
        for ns_tag in [
            f"{{{MEDIA_NS}}}content",
            f"{{{MEDIA_NS}}}thumbnail",
        ]:
            el = e.find(ns_tag)
            if el is not None:
                img = el.get("url", "")
                if img:
                    break
        if not img:
            enc = e.find("enclosure")
            if enc is not None:
                img = enc.get("url", "")
        if not img:
            for raw_tag in [
                f"{{{CONTENT_NS}}}encoded",
                "description",
            ]:
                el = e.find(raw_tag)
                if el is not None and el.text:
                    img = first_img(el.text)
                    if img:
                        break

        # Categories
        cats = [
            (c.text or c.get("term", "")).strip()
            for c in e.findall("category")
            if c.text or c.get("term")
        ]

        items.append(
            dict(title=title, link=link, desc=desc,
                 source=source, date=date_obj, img=img, cats=cats)
        )

    return items


# ── Section builder ───────────────────────────────────────────────────────────
def is_ghana_political(item: dict) -> bool:
    blob = (item["title"] + " " + " ".join(item["cats"])).lower()
    return any(kw in blob for kw in GHANA_POLITICS_WORDS)


def build_section(keys: list[str], cutoff: datetime,
                  exclude_fn=None, max_n: int = MAX_PER_SECTION) -> list[dict]:
    raw = []
    for key in keys:
        for source, url in FEEDS.get(key, []):
            print(f"  fetching [{key}] {source} …", file=sys.stderr)
            xml = fetch(url)
            if xml:
                raw.extend(parse_feed(xml, source, cutoff))

    if exclude_fn:
        raw = [i for i in raw if not exclude_fn(i)]

    # Deduplicate by normalised title prefix
    seen, unique = set(), []
    for item in raw:
        key = item["title"].lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append(item)

    unique.sort(
        key=lambda x: x["date"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return unique[:max_n]


# ── HTML ──────────────────────────────────────────────────────────────────────
def fmt_date(dt: datetime | None) -> str:
    return dt.strftime("%b %d") if dt else ""


def card(item: dict) -> str:
    img_block = (
        f'<img class="card-img" src="{item["img"]}" alt="" loading="lazy" '
        f'onerror="this.style.display=\'none\'">'
        if item["img"] else ""
    )
    desc_block = f'<p class="card-desc">{item["desc"]}</p>' if item["desc"] else ""
    meta = item["source"] + (f" · {fmt_date(item['date'])}" if item["date"] else "")
    return f"""
    <a class="card" href="{item['link']}" target="_blank" rel="noopener noreferrer">
      {img_block}
      <div class="card-body">
        <span class="card-meta">{meta}</span>
        <h3 class="card-title">{item['title']}</h3>
        {desc_block}
      </div>
    </a>"""


def section(icon: str, title: str, items: list[dict]) -> str:
    if not items:
        return (
            f'<section><h2 class="section-title">{icon} {title}</h2>'
            f'<p class="empty">No recent stories found.</p></section>'
        )
    grid = "\n".join(card(i) for i in items)
    return f"""
  <section>
    <h2 class="section-title">{icon} {title}</h2>
    <div class="card-grid">{grid}
    </div>
  </section>"""


CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:       #f4f4ef;
  --surface:  #ffffff;
  --border:   #e5e5dd;
  --text:     #1a1a1a;
  --muted:    #6b7280;
  --accent:   #1d4ed8;
  --accent-bg:#eff6ff;
  --radius:   12px;
  --shadow:   0 1px 3px rgba(0,0,0,.07), 0 1px 2px rgba(0,0,0,.05);
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

/* ── Header ── */
.header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 16px;
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(8px);
}
.header-inner {
  max-width: 960px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}
.header h1 {
  font-size: 1.15rem;
  font-weight: 800;
  letter-spacing: -.03em;
}
.header p {
  font-size: .75rem;
  color: var(--muted);
  margin-top: 1px;
}
.date-badge {
  background: var(--accent-bg);
  color: var(--accent);
  font-size: .7rem;
  font-weight: 700;
  padding: 4px 10px;
  border-radius: 99px;
  white-space: nowrap;
  flex-shrink: 0;
}

/* ── Layout ── */
main {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 16px 64px;
  display: flex;
  flex-direction: column;
  gap: 36px;
}

/* ── Section ── */
.section-title {
  font-size: .95rem;
  font-weight: 700;
  letter-spacing: -.01em;
  margin-bottom: 14px;
  padding-bottom: 10px;
  border-bottom: 2px solid var(--border);
}

/* ── Grid ── */
.card-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}
@media (min-width: 560px) {
  .card-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (min-width: 840px) {
  .card-grid { grid-template-columns: repeat(3, 1fr); }
}

/* ── Card ── */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow);
  text-decoration: none;
  color: inherit;
  display: flex;
  flex-direction: column;
  transition: box-shadow .15s, transform .15s;
  -webkit-tap-highlight-color: transparent;
}
.card:hover { box-shadow: 0 6px 18px rgba(0,0,0,.1); transform: translateY(-2px); }
.card:active { transform: none; }

.card-img {
  width: 100%;
  height: 168px;
  object-fit: cover;
  display: block;
  background: var(--bg);
}
.card-body {
  padding: 12px 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  flex: 1;
}
.card-meta {
  font-size: .67rem;
  color: var(--muted);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .04em;
}
.card-title {
  font-size: .88rem;
  font-weight: 600;
  line-height: 1.35;
}
.card-desc {
  font-size: .78rem;
  color: var(--muted);
  line-height: 1.5;
}
.empty { color: var(--muted); font-size: .85rem; padding: 16px 0; }

/* ── Footer ── */
footer {
  text-align: center;
  padding: 20px 16px 40px;
  font-size: .7rem;
  color: var(--muted);
  border-top: 1px solid var(--border);
  background: var(--surface);
}
"""


def build_html(sections_data: dict, from_dt: datetime, to_dt: datetime) -> str:
    from_str = from_dt.strftime("%b %d")
    to_str   = to_dt.strftime("%b %d, %Y")
    gen_str  = to_dt.strftime("%A %d %B %Y · %H:%M UTC")

    sections_html = "\n".join(
        section(SECTION_META[k]["icon"], SECTION_META[k]["title"], sections_data[k])
        for k in SECTION_META
    )
    sources = "BBC · The Verge · Hacker News · TechCrunch · JoyOnline"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="theme-color" content="#ffffff">
  <title>Daily Briefing · {to_str}</title>
  <style>{CSS}</style>
</head>
<body>

<header class="header">
  <div class="header-inner">
    <div>
      <h1>📰 Daily Briefing</h1>
      <p>Your world in 5 minutes</p>
    </div>
    <span class="date-badge">News: {from_str} – {to_str}</span>
  </div>
</header>

<main>
{sections_html}
</main>

<footer>
  Generated {gen_str} · {sources}
</footer>

</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=LOOKBACK_DAYS)

    print(f"Generating briefing ({cutoff.strftime('%b %d')} → {now.strftime('%b %d %Y')}) …",
          file=sys.stderr)

    data = {
        "world":        build_section(["world"],        cutoff),
        "tech":         build_section(["tech"],         cutoff),
        "business":     build_section(["business"],     cutoff),
        "ghana":        build_section(["ghana"],        cutoff, exclude_fn=is_ghana_political),
        "ghana_health": build_section(["ghana_health"], cutoff),
    }

    html = build_html(data, cutoff, now)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    total = sum(len(v) for v in data.values())
    print(f"✓ index.html written ({total} stories)", file=sys.stderr)


if __name__ == "__main__":
    main()
