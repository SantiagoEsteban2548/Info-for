"""Universo v1: cripto y metales en perpetuos. Cada activo dice dónde se opera y qué noticias leer."""
from __future__ import annotations

from dataclasses import dataclass

from .news import RedditSource, RSSSource, TradingViewIdeasSource, XSource

CRYPTO_RSS = [
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
]
METALS_RSS = [
    ("kitco", "https://www.kitco.com/rss/KitcoNews.xml"),
    ("fxstreet-commodities", "https://www.fxstreet.com/rss/news"),
]


@dataclass(frozen=True)
class Asset:
    key: str
    name: str
    exchange: str             # binance | bingx
    symbol: str               # símbolo ccxt del perpetuo (BingX: oro = GOLD(XAU)-USDT, plata = SILVER(XAG)-USDT)
    kind: str                 # crypto | metal
    query: str                # término de búsqueda para Reddit/X
    keywords: tuple[str, ...]
    subreddits: tuple[str, ...]
    tv_symbol: str
    max_risk_pct: float       # tope de riesgo por idea para este activo

    def sources(self) -> list:
        rss = CRYPTO_RSS if self.kind == "crypto" else METALS_RSS
        return [
            RedditSource(self.subreddits, query=self.query),
            XSource(self.query),
            TradingViewIdeasSource(self.tv_symbol),
            *[RSSSource(n, u, self.keywords) for n, u in rss],
        ]


ASSETS: dict[str, Asset] = {a.key: a for a in [
    Asset("BTC", "Bitcoin", "binance", "BTC/USDT:USDT", "crypto", "bitcoin",
          ("bitcoin", "btc"), ("Bitcoin", "CryptoCurrency"), "BINANCE:BTCUSDT.P", 2.0),
    Asset("ETH", "Ethereum", "binance", "ETH/USDT:USDT", "crypto", "ethereum",
          ("ethereum", "eth", "ether"), ("ethereum", "CryptoCurrency"), "BINANCE:ETHUSDT.P", 2.0),
    Asset("XAU", "Oro", "bingx", "NCCOGOLD2USD/USDT:USDT", "metal", "gold price",
          ("gold", "xau", "bullion"), ("Gold", "Commodities"), "OANDA:XAUUSD", 3.0),
    Asset("XAG", "Plata", "bingx", "NCCOXAG2USD/USDT:USDT", "metal", "silver price",
          ("silver", "xag"), ("Silverbugs", "Commodities"), "OANDA:XAGUSD", 3.0),
]}
