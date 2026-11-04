import json
from datetime import datetime, timezone

from tradingagent.news import (RedditSource, RSSSource, TradingViewIdeasSource, XSource,
                               collect_news, parse_reddit_json, parse_rss)

NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)

REDDIT = json.dumps({"data": {"children": [
    {"data": {"title": "BTC breakout above range", "selftext": "ETF inflow strong",
              "permalink": "/r/Bitcoin/1", "created_utc": NOW.timestamp() - 3600, "score": 120}},
    {"data": {"title": "Daily thread", "stickied": True, "permalink": "/r/Bitcoin/2",
              "created_utc": NOW.timestamp()}},
    {"data": {"title": "Old news", "permalink": "/r/Bitcoin/3",
              "created_utc": NOW.timestamp() - 30 * 86400}},
]}})

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Gold rallies on rate cut bets</title><link>https://x.test/a</link>
<description>&lt;p&gt;Bullion &lt;b&gt;up&lt;/b&gt; 2%&lt;/p&gt;</description>
<pubDate>Fri, 06 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Oil slips</title><link>https://x.test/b</link><description>crude</description>
<pubDate>Fri, 06 Nov 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>BTC idea: long</title><link href="https://tv.test/i/1"/>
<summary>Bull flag</summary><updated>2026-11-06T08:00:00Z</updated></entry></feed>"""


def test_parse_reddit_skips_stickied():
    items = parse_reddit_json(REDDIT)
    assert [i.title for i in items] == ["BTC breakout above range", "Old news"]
    assert items[0].url == "https://www.reddit.com/r/Bitcoin/1" and items[0].score == 120


def test_parse_rss_and_atom_strip_html():
    rss = parse_rss(RSS, "rss:kitco")
    assert rss[0].text == "Bullion up 2%" and rss[0].published.day == 6
    atom = parse_rss(ATOM, "tradingview")
    assert atom[0].url == "https://tv.test/i/1" and atom[0].text == "Bull flag"


def test_collect_filters_dedupes_numbers_and_survives_failures():
    pages = {}

    def fetch(url):
        if "reddit.com" in url:
            return REDDIT
        if "tradingview" in url:
            raise OSError("bloqueado")
        return RSS

    sources = [RedditSource(["Bitcoin"], query="bitcoin"), TradingViewIdeasSource("BINANCE:BTCUSDT"),
               RSSSource("kitco", "https://feed", keywords=["gold"]),
               RSSSource("kitco", "https://feed", keywords=["gold"]),   # duplicado
               XSource("bitcoin", template="")]                         # sin puente: no hace nada
    items, errors = collect_news(sources, fetch=fetch, now=NOW)
    titles = [i.title for i in items]
    assert titles == ["BTC breakout above range", "Gold rallies on rate cut bets"]
    assert [i.id for i in items] == ["N1", "N2"]
    assert len(errors) == 1 and "tradingview" in errors[0]
    assert pages == {}


def test_x_source_uses_configured_bridge():
    seen = []
    XSource("gold price", template="https://bridge.test/x/{query}").fetch(
        lambda u: seen.append(u) or ATOM)
    assert seen == ["https://bridge.test/x/gold%20price"]
