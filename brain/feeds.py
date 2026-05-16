import hashlib
import feedparser
from datetime import datetime, timezone, timedelta

# Configured RSS sources — (display_name, feed_url)
FEED_SOURCES = [
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt",       "https://decrypt.co/feed"),
    ("The Block",     "https://www.theblock.co/rss.xml"),
    ("Solana News",   "https://solana.com/news/rss.xml"),
]


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def fetch_feed(name: str, url: str, max_age_hours: int = 6) -> list[dict]:
    """Fetch one RSS feed. Returns items published within max_age_hours."""
    try:
        parsed = feedparser.parse(url)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        items  = []
        for entry in parsed.entries:
            link  = entry.get("link", "").strip()
            title = entry.get("title", "").strip()
            if not link or not title:
                continue
            published = None
            if getattr(entry, "published_parsed", None):
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            if published and published < cutoff:
                continue
            items.append({
                "source":    name,
                "title":     title,
                "url":       link,
                "published": published,
                "hash":      _url_hash(link),
            })
        return items
    except Exception:
        return []


def fetch_new_items(seen_hashes: set, max_age_hours: int = 6) -> list[dict]:
    """Fetch all sources, return only items not in seen_hashes."""
    new_items = []
    for name, url in FEED_SOURCES:
        for item in fetch_feed(name, url, max_age_hours):
            if item["hash"] not in seen_hashes:
                new_items.append(item)
    return new_items


def seed_seen(max_age_hours: int = 6) -> set:
    """On startup, mark current feed state as already seen so we don't
    flood the channel with backlog on first run."""
    seen = set()
    for name, url in FEED_SOURCES:
        for item in fetch_feed(name, url, max_age_hours):
            seen.add(item["hash"])
    return seen


def format_headline(item: dict) -> str:
    return f"[{item['source']}] {item['title']} — {item['url']}"
