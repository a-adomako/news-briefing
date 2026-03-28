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
    "world":        {"icon": "🌍", "title": "World News",         "slug": "world"},
    "tech":         {"icon": "💻", "title": "Tech",               "slug": "tech"},
    "business":     {"icon": "📈", "title": "Business & Economy", "slug": "biz"},
    "ghana":        {"icon": "🇬🇭", "title": "Ghana",            "slug": "ghana"},
    "ghana_health": {"icon": "🏥", "title": "Ghana Health",       "slug": "health"},
}

GHANA_POLITICS_WORDS = [
    "ndc", "npp", "sole-sourc", "sole source", "big push",
    " mp ", " mps ", "parliament", "constituency",
    "akufo-addo", "bawumia", "mahama",
]

MEDIA_NS   = "http://search.yahoo.com/mrss/"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
ATOM_NS    = "http://www.w3.org/2005/Atom"


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

    items = []
    for e in entries:
        # Title
        title = strip_tags(
            (e.findtext(f"{{{ATOM_NS}}}title") or "") if is_atom
            else (e.findtext("title") or "")
        )
        if not title:
            continue

        # Link
        if is_atom:
            link_el = e.find(f"{{{ATOM_NS}}}link")
            link = link_el.get("href", "") if link_el is not None else ""
        else:
            link = (e.findtext("link") or "").strip()

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
            date_obj = parse_date(e.findtext("pubDate") or "")

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
            raw = e.findtext("description") or ""
            if raw:
                desc = truncate(strip_tags(raw))
            if not desc:
                el = e.find(f"{{{CONTENT_NS}}}encoded")
                if el is not None and el.text:
                    desc = truncate(strip_tags(el.text))

        # Image
        img = ""
        for ns_tag in [f"{{{MEDIA_NS}}}content", f"{{{MEDIA_NS}}}thumbnail"]:
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
            for raw_tag in [f"{{{CONTENT_NS}}}encoded", "description"]:
                el = e.find(raw_tag)
                if el is not None and el.text:
                    img = first_img(el.text)
                    if img:
                        break

        items.append(dict(
            title=title, link=link, desc=desc,
            source=source, date=date_obj, img=img,
        ))

    return items


# ── Section builder ───────────────────────────────────────────────────────────
def is_ghana_political(item: dict) -> bool:
    blob = item["title"].lower()
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

    seen, unique = set(), []
    for item in raw:
        k = item["title"].lower()[:60]
        if k not in seen:
            seen.add(k)
            unique.append(item)

    unique.sort(
        key=lambda x: x["date"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return unique[:max_n]


# ── HTML helpers ──────────────────────────────────────────────────────────────
def fmt_date(dt: datetime | None) -> str:
    return dt.strftime("%b %d, %Y") if dt else ""


def card_html(item: dict, slug: str, section_icon: str, is_lead: bool = False) -> str:
    if item["img"]:
        img_block = (
            f'<img class="card-img" src="{item["img"]}" alt="" loading="lazy" '
            f'onerror="this.style.display=\'none\'">'
        )
    else:
        img_block = f'<div class="card-img-placeholder">{section_icon}</div>'

    desc_block = f"<p>{item['desc']}</p>" if item["desc"] else ""
    date_str   = fmt_date(item["date"])

    return f"""    <div class="card{' lead-card' if is_lead else ''}">
      {img_block}
      <div class="card-body">
        <div class="card-meta">
          <span class="card-source source-{slug}">{item['source']}</span>
          <span class="card-date">{date_str}</span>
        </div>
        <h3>{item['title']}</h3>
        {desc_block}
        <div class="card-footer">
          <a class="read-link read-link-{slug}" href="{item['link']}" target="_blank" rel="noopener noreferrer">Read on {item['source']} →</a>
        </div>
      </div>
    </div>"""


def section_html(key: str, items: list[dict]) -> str:
    meta = SECTION_META[key]
    icon, title, slug = meta["icon"], meta["title"], meta["slug"]

    if not items:
        return f"""
  <div class="section-header">
    <div class="section-icon section-icon-{slug}">{icon}</div>
    <div class="section-title-wrap"><h2>{title}</h2></div>
    <div class="section-line"></div>
  </div>
  <p style="color:var(--muted);font-size:14px;padding:16px 0">No recent stories found.</p>"""

    cards = "\n".join(
        card_html(item, slug, icon, is_lead=(i == 0))
        for i, item in enumerate(items)
    )
    return f"""
  <div class="section-header">
    <div class="section-icon section-icon-{slug}">{icon}</div>
    <div class="section-title-wrap"><h2>{title}</h2></div>
    <div class="section-line"></div>
  </div>
  <div class="cards-grid">
{cards}
  </div>"""


# ── CSS ───────────────────────────────────────────────────────────────────────
CSS = """
    :root {
      --bg: #f8f9fb;
      --surface: #ffffff;
      --surface2: #f1f3f7;
      --border: #e2e6ef;
      --accent-world:  #2563eb;
      --accent-tech:   #7c3aed;
      --accent-biz:    #059669;
      --accent-ghana:  #d97706;
      --accent-health: #0891b2;
      --text: #0f172a;
      --muted: #64748b;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      line-height: 1.6;
      -webkit-font-smoothing: antialiased;
    }

    /* HEADER */
    .header {
      background: #ffffff;
      border-bottom: 1px solid var(--border);
      padding: 40px 24px 32px;
      text-align: center;
    }
    .header-eyebrow {
      font-size: 11px; font-weight: 700;
      letter-spacing: 0.18em; text-transform: uppercase;
      color: var(--muted); margin-bottom: 10px;
    }
    .header h1 {
      font-size: clamp(26px, 6vw, 42px);
      font-weight: 800; letter-spacing: -1.5px;
      color: var(--text); margin-bottom: 6px;
    }
    .header-sub { font-size: 14px; color: var(--muted); }
    .date-range-badge {
      display: inline-flex; align-items: center; gap: 6px;
      background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d;
      border-radius: 999px; padding: 4px 14px;
      font-size: 12px; font-weight: 600; margin-top: 14px;
    }
    .pills {
      display: flex; gap: 8px; justify-content: center;
      flex-wrap: wrap; margin-top: 16px;
    }
    .pill {
      display: inline-flex; align-items: center; gap: 5px;
      padding: 4px 12px; border-radius: 999px;
      font-size: 12px; font-weight: 600; border: 1.5px solid;
    }
    .pill-world  { color: var(--accent-world);  border-color: #bfdbfe; background: #eff6ff; }
    .pill-tech   { color: var(--accent-tech);   border-color: #ddd6fe; background: #f5f3ff; }
    .pill-biz    { color: var(--accent-biz);    border-color: #a7f3d0; background: #f0fdf4; }
    .pill-ghana  { color: var(--accent-ghana);  border-color: #fde68a; background: #fffbeb; }
    .pill-health { color: var(--accent-health); border-color: #a5f3fc; background: #ecfeff; }

    /* LAYOUT */
    .container { max-width: 1200px; margin: 0 auto; padding: 0 20px 80px; }

    /* SECTION HEADER */
    .section-header {
      display: flex; align-items: center; gap: 14px;
      padding: 48px 0 20px;
    }
    .section-icon {
      width: 40px; height: 40px; border-radius: 10px;
      display: flex; align-items: center; justify-content: center;
      font-size: 20px; flex-shrink: 0;
    }
    .section-icon-world  { background: #eff6ff; }
    .section-icon-tech   { background: #f5f3ff; }
    .section-icon-biz    { background: #f0fdf4; }
    .section-icon-ghana  { background: #fffbeb; }
    .section-icon-health { background: #ecfeff; }
    .section-title-wrap h2 {
      font-size: 20px; font-weight: 700; letter-spacing: -0.3px;
    }
    .section-line { flex: 1; height: 1px; background: var(--border); }

    /* CARD GRID */
    .cards-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 16px;
    }
    @media (max-width: 640px) { .cards-grid { grid-template-columns: 1fr; } }

    /* CARD */
    .card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 14px; overflow: hidden;
      display: flex; flex-direction: column;
      transition: box-shadow .15s, transform .15s;
      -webkit-tap-highlight-color: transparent;
    }
    .card:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,.08); }
    .card:active { transform: none; }
    .card-img {
      width: 100%; height: 190px; object-fit: cover;
      background: var(--surface2); display: block;
    }
    .card-img-placeholder {
      width: 100%; height: 140px; background: var(--surface2);
      display: flex; align-items: center; justify-content: center;
      font-size: 40px;
    }
    .card-body {
      padding: 16px 18px 18px; flex: 1;
      display: flex; flex-direction: column;
    }
    .card-meta {
      display: flex; align-items: center;
      justify-content: space-between; margin-bottom: 8px;
    }
    .card-source {
      font-size: 10px; font-weight: 700;
      letter-spacing: .1em; text-transform: uppercase;
      padding: 2px 8px; border-radius: 4px;
    }
    .source-world  { color: var(--accent-world);  background: #eff6ff; }
    .source-tech   { color: var(--accent-tech);   background: #f5f3ff; }
    .source-biz    { color: var(--accent-biz);    background: #f0fdf4; }
    .source-ghana  { color: var(--accent-ghana);  background: #fffbeb; }
    .source-health { color: var(--accent-health); background: #ecfeff; }
    .card-date { font-size: 11px; color: var(--muted); }
    .card h3 {
      font-size: 15px; font-weight: 700; line-height: 1.4;
      letter-spacing: -0.2px; margin-bottom: 8px;
    }
    .card p { font-size: 13px; color: var(--muted); line-height: 1.65; flex: 1; }
    .card-footer {
      margin-top: 14px; padding-top: 10px;
      border-top: 1px solid var(--border);
    }
    .read-link {
      font-size: 12px; font-weight: 600; text-decoration: none;
      display: inline-flex; align-items: center; gap: 4px;
    }
    .read-link-world  { color: var(--accent-world); }
    .read-link-tech   { color: var(--accent-tech); }
    .read-link-biz    { color: var(--accent-biz); }
    .read-link-ghana  { color: var(--accent-ghana); }
    .read-link-health { color: var(--accent-health); }
    .read-link:hover { text-decoration: underline; }

    /* LEAD CARD */
    .lead-card {
      grid-column: 1 / -1;
      display: grid; grid-template-columns: 1.1fr 1fr;
    }
    .lead-card .card-img { height: 100%; min-height: 240px; border-radius: 0; }
    .lead-card .card-body { padding: 24px 28px; justify-content: center; }
    .lead-card h3 { font-size: 20px; }
    .lead-card p  { font-size: 14px; }
    @media (max-width: 640px) {
      .lead-card { grid-template-columns: 1fr; }
      .lead-card .card-img { height: 200px; }
    }

    /* FOOTER */
    .page-footer {
      text-align: center; padding: 24px 20px;
      border-top: 1px solid var(--border);
      font-size: 12px; color: var(--muted); background: #fff;
    }
"""


# ── Full page ─────────────────────────────────────────────────────────────────
def build_html(sections_data: dict, from_dt: datetime, to_dt: datetime) -> str:
    from_str = from_dt.strftime("%B %d")
    to_str   = to_dt.strftime("%B %d, %Y")
    day_str  = to_dt.strftime("%A, %B %d, %Y")
    gen_str  = to_dt.strftime("%d %b %Y · %H:%M UTC")

    all_sections = "\n".join(section_html(k, sections_data[k]) for k in SECTION_META)
    sources = "BBC · The Verge · Hacker News · TechCrunch · JoyOnline"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="theme-color" content="#ffffff">
  <title>Daily Briefing — {to_str}</title>
  <style>{CSS}</style>
</head>
<body>

<div class="header">
  <div class="header-eyebrow">AI-Curated Daily Briefing</div>
  <h1>Daily Briefing</h1>
  <div class="header-sub">{day_str}</div>
  <div class="date-range-badge">📅 News from {from_str} – {to_str}</div>
  <div class="pills">
    <span class="pill pill-world">🌍 World News</span>
    <span class="pill pill-tech">💻 Tech</span>
    <span class="pill pill-biz">📈 Business &amp; Economy</span>
    <span class="pill pill-ghana">🇬🇭 Ghana</span>
    <span class="pill pill-health">🏥 Ghana Health</span>
  </div>
</div>

<div class="container">
{all_sections}
</div>

<div class="page-footer">
  Generated {gen_str} · {sources}
</div>

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
