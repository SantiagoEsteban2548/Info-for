"""Fuentes de noticias gratuitas. Todo lo que llega de aquí es texto de terceros: dato, no instrucción.

- Reddit: endpoints JSON públicos (sin clave, con límite de uso).
- X: no hay API de lectura gratuita. Se lee a través de un puente RSS que configures tú
  (por ejemplo una instancia propia de RSSHub o Nitter) con la variable TRADINGAGENT_X_RSS.
- TradingView: no tiene API pública; se usa el feed RSS de ideas por símbolo, que puede
  cambiar o desaparecer sin aviso. Si falla, el pipeline sigue sin esa fuente.
- RSS de medios (CoinDesk, Cointelegraph, Kitco...) para lo fundamental.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Iterable

USER_AGENT = "tradingagent/0.1 (research bot)"
Fetcher = Callable[[str], str]


def http_get(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URLs fijas o configuradas)
        return resp.read().decode("utf-8", errors="replace")


@dataclass(frozen=True)
class NewsItem:
    source: str               # reddit | x | tradingview | rss:<nombre>
    title: str
    text: str
    url: str
    published: datetime
    score: float = 0.0        # votos/likes si la fuente los da
    id: str = field(default="", compare=False)

    def short(self, max_chars: int = 400) -> str:
        body = " ".join(self.text.split())[:max_chars]
        return f"[{self.id}] ({self.source}, {self.published:%Y-%m-%d}) {self.title} — {body}"


def _strip_html(s: str) -> str:
    out, inside = [], False
    for ch in s or "":
        if ch == "<":
            inside = True
        elif ch == ">":
            inside = False
        elif not inside:
            out.append(ch)
    return " ".join("".join(out).split())


def _parse_date(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_reddit_json(payload: str) -> list[NewsItem]:
    data = json.loads(payload)
    items = []
    for child in data.get("data", {}).get("children", []):
        p = child.get("data", {})
        if p.get("stickied") or p.get("over_18"):
            continue
        items.append(NewsItem(
            source="reddit", title=p.get("title", ""), text=p.get("selftext", "")[:2000],
            url="https://www.reddit.com" + p.get("permalink", ""),
            published=datetime.fromtimestamp(float(p.get("created_utc", 0)), timezone.utc),
            score=float(p.get("score", 0))))
    return items


def parse_rss(payload: str, source: str) -> list[NewsItem]:
    """RSS 2.0 y Atom."""
    root = ET.fromstring(payload)
    atom = "{http://www.w3.org/2005/Atom}"
    items = []
    for it in root.iter("item"):
        items.append(NewsItem(
            source=source, title=(it.findtext("title") or "").strip(),
            text=_strip_html(it.findtext("description") or ""),
            url=(it.findtext("link") or "").strip(), published=_parse_date(it.findtext("pubDate"))))
    for it in root.iter(f"{atom}entry"):
        link = it.find(f"{atom}link")
        items.append(NewsItem(
            source=source, title=(it.findtext(f"{atom}title") or "").strip(),
            text=_strip_html(it.findtext(f"{atom}summary") or it.findtext(f"{atom}content") or ""),
            url=link.get("href", "") if link is not None else "",
            published=_parse_date(it.findtext(f"{atom}updated") or it.findtext(f"{atom}published"))))
    return items


class RedditSource:
    name = "reddit"

    def __init__(self, subreddits: Iterable[str], query: str | None = None, limit: int = 25):
        self.subreddits, self.query, self.limit = list(subreddits), query, limit

    def fetch(self, fetch: Fetcher) -> list[NewsItem]:
        items = []
        for sub in self.subreddits:
            if self.query:
                q = urllib.parse.quote(self.query)
                url = (f"https://www.reddit.com/r/{sub}/search.json?q={q}&restrict_sr=1"
                       f"&sort=new&t=week&limit={self.limit}")
            else:
                url = f"https://www.reddit.com/r/{sub}/top.json?t=week&limit={self.limit}"
            items += parse_reddit_json(fetch(url))
        return items


class RSSSource:
    def __init__(self, name: str, url: str, keywords: Iterable[str] = ()):
        self.name, self.url, self.keywords = f"rss:{name}", url, [k.lower() for k in keywords]

    def fetch(self, fetch: Fetcher) -> list[NewsItem]:
        items = parse_rss(fetch(self.url), self.name)
        if not self.keywords:
            return items
        return [i for i in items if any(k in (i.title + " " + i.text).lower() for k in self.keywords)]


class XSource:
    """Posts de X a través de un puente RSS. Plantilla con {query}, p. ej.
    TRADINGAGENT_X_RSS="https://mi-rsshub.example/twitter/keyword/{query}"."""
    name = "x"

    def __init__(self, query: str, template: str | None = None):
        self.query = query
        self.template = template if template is not None else os.environ.get("TRADINGAGENT_X_RSS", "")

    def fetch(self, fetch: Fetcher) -> list[NewsItem]:
        if not self.template:
            return []
        url = self.template.format(query=urllib.parse.quote(self.query))
        return parse_rss(fetch(url), "x")


class TradingViewIdeasSource:
    """Ideas publicadas en TradingView para un símbolo (feed RSS no oficial)."""
    name = "tradingview"

    def __init__(self, tv_symbol: str):
        self.tv_symbol = tv_symbol

    def fetch(self, fetch: Fetcher) -> list[NewsItem]:
        url = f"https://www.tradingview.com/feed/?sort=recent&symbol={urllib.parse.quote(self.tv_symbol)}"
        return parse_rss(fetch(url), "tradingview")


def collect_news(sources: Iterable, fetch: Fetcher = http_get, days: int = 7,
                 max_items: int = 40, now: datetime | None = None) -> tuple[list[NewsItem], list[str]]:
    """Junta las fuentes, filtra por fecha, quita duplicados y numera (N1, N2...) para citar.

    Devuelve (noticias, errores). Una fuente caída no detiene el pipeline.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    seen, items, errors = set(), [], []
    for src in sources:
        try:
            got = src.fetch(fetch)
        except Exception as exc:  # red, formato, bloqueo: se registra y se sigue
            errors.append(f"{getattr(src, 'name', src)}: {type(exc).__name__}: {exc}")
            continue
        for it in got:
            key = (it.title.strip().lower(), it.url)
            if it.published < cutoff or not it.title or key in seen:
                continue
            seen.add(key)
            items.append(it)
    # Cada fuente aporta lo más reciente y lo más votado; tope global para el contexto del modelo.
    items.sort(key=lambda i: (i.published, i.score), reverse=True)
    items = items[:max_items]
    numbered = [NewsItem(i.source, i.title, i.text, i.url, i.published, i.score, f"N{k}")
                for k, i in enumerate(items, 1)]
    return numbered, errors
