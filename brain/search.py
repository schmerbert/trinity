import json
import urllib.request
import urllib.parse


def ddg_search(query, max_results=6):
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddg:
            results = list(ddg.text(query, max_results=min(max_results, 10)))
        return [
            {"title": r["title"], "url": r["href"], "snippet": r["body"]}
            for r in results
        ]
    except Exception as e:
        return {"error": str(e)}


def get_coin_data(query):
    try:
        headers = {"User-Agent": "Trinity/1.0"}

        search_url = f"https://api.coingecko.com/api/v3/search?query={urllib.parse.quote(query)}"
        req = urllib.request.Request(search_url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            search_data = json.loads(resp.read())

        coins = search_data.get("coins", [])
        if not coins:
            return {"error": f"No coin found for '{query}'"}

        coin_id = coins[0]["id"]
        name    = coins[0]["name"]
        symbol  = coins[0]["symbol"].upper()

        price_url = (
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={coin_id}&vs_currencies=usd"
            f"&include_24hr_change=true&include_market_cap=true&include_24hr_vol=true"
        )
        req = urllib.request.Request(price_url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            price_data = json.loads(resp.read())

        d = price_data.get(coin_id, {})
        return {
            "name":            name,
            "symbol":          symbol,
            "price_usd":       d.get("usd"),
            "change_24h_pct":  round(d.get("usd_24h_change") or 0, 2),
            "market_cap_usd":  d.get("usd_market_cap"),
            "volume_24h_usd":  d.get("usd_24h_vol"),
        }
    except Exception as e:
        return {"error": str(e)}


def get_dex_data(query):
    try:
        url = f"https://api.dexscreener.com/latest/dex/search?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Trinity/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())

        pairs = data.get("pairs") or []
        if not pairs:
            return {"error": f"No DEX pairs found for '{query}'"}

        pairs = sorted(
            pairs,
            key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
            reverse=True
        )
        results = []
        for p in pairs[:3]:
            base  = p.get("baseToken") or {}
            quote = p.get("quoteToken") or {}
            results.append({
                "pair":           f"{base.get('name')} / {quote.get('symbol')}",
                "dex":            p.get("dexId"),
                "chain":          p.get("chainId"),
                "price_usd":      p.get("priceUsd"),
                "change_24h_pct": (p.get("priceChange") or {}).get("h24"),
                "liquidity_usd":  (p.get("liquidity") or {}).get("usd"),
                "volume_24h_usd": (p.get("volume") or {}).get("h24"),
                "url":            p.get("url"),
            })
        return results
    except Exception as e:
        return {"error": str(e)}
