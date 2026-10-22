"""
Lawctopus competitions -> Discord webhook (no bot account needed)

Run once (for cron / Task Scheduler):   python webhook.py
Run forever, checking every N minutes:  python webhook.py --loop
"""
import html
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import feedparser
import requests
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
BASE = "https://www.lawctopus.com"
FEEDS = [u.strip() for u in os.getenv("FEED_URLS", f"{BASE}/feed/").split(",") if u.strip()]
KEYWORDS = [k.strip().lower() for k in os.getenv(
    "KEYWORDS",
    "moot,competition,contest,essay,debate,quiz,negotiation,mediation,arbitration,"
    "hackathon,client counselling,mock trial,symposium",
).split(",") if k.strip()]
POLL_MINUTES = int(os.getenv("POLL_MINUTES", "30"))
SEEN_FILE = Path(os.getenv("SEEN_FILE", "seen.json"))
UA = "Mozilla/5.0 (compatible; LawctopusWebhook/1.0)"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lawctopus-webhook")


def clean(text, limit=300):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def matches(entry):
    if not KEYWORDS:
        return True
    blob = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()
    return any(k in blob for k in KEYWORDS)


def fetch_entries():
    seen, out = set(), []
    for url in FEEDS:
        parsed = feedparser.parse(url, agent=UA)
        if parsed.bozo and not parsed.entries:
            log.warning("Feed failed: %s (%s)", url, parsed.get("bozo_exception"))
        for e in parsed.entries:
            if e.link not in seen:
                seen.add(e.link)
                out.append(e)
    return out


def to_embed(e):
    return {
        "title": clean(e.get("title", "Untitled"), 250),
        "url": e.link,
        "description": clean(e.get("summary", "")),
        "color": 0x2F6FED,
        "footer": {"text": f"Lawctopus • {e.get('published', '')}".strip(" •")},
    }


def send(embeds):
    """Post up to 10 embeds in one webhook message, retrying on rate limits."""
    for _ in range(3):
        r = requests.post(WEBHOOK_URL, json={"username": "Lawctopus", "embeds": embeds}, timeout=15)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        r.raise_for_status()
        return
    raise RuntimeError("Discord rate limit: gave up after 3 tries")


def load_seen():
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            log.warning("Could not read %s, starting fresh", SEEN_FILE)
    return set()


def run_once():
    first_run = not SEEN_FILE.exists()
    seen = load_seen()
    entries = fetch_entries()
    fresh = [e for e in entries if e.link not in seen and matches(e)]

    if first_run:
        fresh = fresh[:5]  # avoid flooding on the very first run

    fresh.reverse()  # oldest first
    for i in range(0, len(fresh), 10):
        send([to_embed(e) for e in fresh[i:i + 10]])
        time.sleep(1)

    # Only mark as seen after posting succeeded
    seen.update(e.link for e in entries)
    SEEN_FILE.write_text(json.dumps(list(seen)[-3000:]))
    log.info("Checked %d entries, posted %d", len(entries), len(fresh))


if __name__ == "__main__":
    if "--loop" in sys.argv:
        while True:
            try:
                run_once()
            except Exception:
                log.exception("Run failed")
            time.sleep(POLL_MINUTES * 60)
    else:
        run_once()
