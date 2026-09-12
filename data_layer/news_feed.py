"""
News feed - Data Layer addition per spec Section 3 (tagged per coin, used
by Product A to flag events that could invalidate a signal).
Uses CoinDesk's public RSS feed - no API key required, genuinely free.
"""
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

COINDESK_RSS = "https://www.coindesk.com/arc/outboundfeeds/rss/"

COIN_KEYWORDS = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "eth"],
    "SOLUSDT": ["solana", "sol"],
    "BNBUSDT": ["binance", "bnb"],
    "XRPUSDT": ["ripple", "xrp"],
}


def fetch_news(symbol: str, limit: int = 5) -> list:
    """
    Fetch recent crypto news and filter for relevance to the given symbol.
    Returns a list of {title, link, published} dicts. Fails gracefully -
    if the feed is unreachable, returns an empty list rather than erroring
    out the whole Chart Assistant response.
    """
    keywords = COIN_KEYWORDS.get(symbol, [])
    try:
        resp = requests.get(COINDESK_RSS, timeout=8)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        items = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pub_date = item.findtext("pubDate", "")

            title_lower = title.lower()
            is_relevant = any(kw in title_lower for kw in keywords) if keywords else True

            if is_relevant:
                items.append({"title": title, "link": link, "published": pub_date})

            if len(items) >= limit:
                break

        return items
    except Exception:
        return []
