import os
import sys
import json
import hashlib
import time
import requests
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from brain.memory import get_profile, save_alert

from pathlib import Path
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

NEWS_API_KEY = os.getenv("NEWS_API_KEY")
SUBREDDITS = os.getenv("REDDIT_SUBREDDITS", "").split(",")
KEYWORDS = [k.strip().lower() for k in os.getenv("KEYWORDS", "").split(",")]

HEADERS = {"User-Agent": "Trinity/1.0 (personal research assistant)"}


def score_relevance(text, profile):
    text_lower = text.lower()
    score = 0.0
    interests = profile.get("interests", [])

    for interest in interests:
        topic = interest.get("topic", "").lower()
        weight = interest.get("weight", 1.0)
        if topic in text_lower:
            score += weight

    for keyword in KEYWORDS:
        if keyword in text_lower:
            score += 0.5

    return round(score, 2)


def generate_hash(alert):
    if alert["source"] == "dexscreener":
        unique_string = f"{alert['headline']}{alert['source']}{alert['topic']}{time.time()}"
    else:
        unique_string = f"{alert['headline']}{alert['source']}{alert['topic']}"
    return hashlib.md5(unique_string.encode()).hexdigest()


def scrape_reddit(profile):
    print("[Eyes] Scanning Reddit...")
    alerts = []

    for subreddit in SUBREDDITS:
        try:
            url = f"https://www.reddit.com/r/{subreddit}/new.json?limit=25"
            response = requests.get(url, headers=HEADERS, timeout=10)

            if response.status_code != 200:
                print(f"[Eyes] Skipping r/{subreddit} — status {response.status_code}")
                continue

            posts = response.json()["data"]["children"]

            for post in posts:
                data = post["data"]
                title = data.get("title", "")
                text = data.get("selftext", "")
                combined = f"{title} {text}".lower()

                matched = any(keyword in combined for keyword in KEYWORDS)
                if not matched:
                    continue

                score = score_relevance(combined, profile)
                if score < 0.5:
                    continue

                alerts.append({
                    "profile_id": profile["id"],
                    "source": f"reddit/r/{subreddit}",
                    "topic": next((k for k in KEYWORDS if k in combined), "general"),
                    "headline": title,
                    "summary": text[:300] if text else title,
                    "url": f"https://reddit.com{data.get('permalink', '')}",
                    "relevance_score": score
                })

        except Exception as e:
            print(f"[Eyes] Reddit error on r/{subreddit}: {e}")

    return alerts


def scrape_news(profile):
    print("[Eyes] Scanning news...")
    alerts = []

    for keyword in KEYWORDS:
        try:
            url = f"https://newsapi.org/v2/everything?q={keyword}&sortBy=publishedAt&pageSize=5&apiKey={NEWS_API_KEY}"
            response = requests.get(url, timeout=10)

            if response.status_code != 200:
                continue

            articles = response.json().get("articles", [])

            for article in articles:
                title = article.get("title", "")
                description = article.get("description", "") or ""
                combined = f"{title} {description}".lower()

                score = score_relevance(combined, profile)
                if score < 0.5:
                    continue

                alerts.append({
                    "profile_id": profile["id"],
                    "source": "newsapi",
                    "topic": keyword,
                    "headline": title,
                    "summary": description[:300],
                    "url": article.get("url", ""),
                    "relevance_score": score
                })

        except Exception as e:
            print(f"[Eyes] News error for {keyword}: {e}")

    return alerts


def scrape_crypto_prices(profile):
    print("[Eyes] Checking crypto prices via DexScreener...")
    alerts = []

    tokens = {
        "troll": os.getenv("TROLL_CA"),
        "wish": os.getenv("WISH_CA")
    }

    for name, ca in tokens.items():
        if not ca:
            continue
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
            response = requests.get(url, timeout=10)

            if response.status_code != 200:
                print(f"[Eyes] DexScreener error for {name}: {response.status_code}")
                continue

            data = response.json()
            pairs = data.get("pairs", [])

            if not pairs:
                print(f"[Eyes] No pairs found for {name}")
                continue

            pair = sorted(pairs, key=lambda x: float(x.get("liquidity", {}).get("usd", 0)), reverse=True)[0]

            token_name = pair.get("baseToken", {}).get("name", name)
            symbol = pair.get("baseToken", {}).get("symbol", name.upper())
            price = float(pair.get("priceUsd", 0))
            change_24h = float(pair.get("priceChange", {}).get("h24", 0))
            volume_24h = float(pair.get("volume", {}).get("h24", 0))
            liquidity = float(pair.get("liquidity", {}).get("usd", 0))
            dex_url = pair.get("url", f"https://dexscreener.com/solana/{ca}")

            summary = (
                f"{token_name} ({symbol}) — "
                f"Price: ${price:.8f} | "
                f"24h Change: {change_24h:+.2f}% | "
                f"24h Volume: ${volume_24h:,.0f} | "
                f"Liquidity: ${liquidity:,.0f}"
            )

            alerts.append({
                "profile_id": profile["id"],
                "source": "dexscreener",
                "topic": name,
                "headline": f"{token_name} ({symbol}) at ${price:.8f} ({change_24h:+.2f}% 24h)",
                "summary": summary,
                "url": dex_url,
                "relevance_score": 2.0
            })

            print(f"[Eyes] {summary}")

        except Exception as e:
            print(f"[Eyes] Price error for {name}: {e}")

    return alerts


def run_eyes():
    profile = get_profile()
    if not profile:
        print("[Eyes] No profile found, cannot scan.")
        return

    all_alerts = []
    all_alerts.extend(scrape_crypto_prices(profile))
    all_alerts.extend(scrape_reddit(profile))
    all_alerts.extend(scrape_news(profile))

    seen_urls = set()
    deduped_alerts = []
    for alert in all_alerts:
        if alert["source"] == "dexscreener":
            deduped_alerts.append(alert)
        elif alert["url"] not in seen_urls:
            seen_urls.add(alert["url"])
            deduped_alerts.append(alert)

    deduped_alerts.sort(key=lambda x: x["relevance_score"], reverse=True)

    print(f"\n[Eyes] Found {len(deduped_alerts)} unique relevant items.")

    for alert in deduped_alerts[:15]:
        alert["content_hash"] = generate_hash(alert)
        saved = save_alert(alert)
        if saved:
            print(f"  [NEW] [{alert['relevance_score']}] {alert['headline'][:80]}")
        else:
            print(f"  [DUP] [{alert['relevance_score']}] {alert['headline'][:80]}")


if __name__ == "__main__":
    run_eyes()