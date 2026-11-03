"""Bounded search snippets from Tavily, or explicitly selected public RSS.

API reference: https://docs.tavily.com/documentation/api-reference/endpoint/search
No result page is fetched, and no provider-generated answer is used as evidence.
"""

import asyncio
import json
from datetime import datetime, timezone

import aiohttp

try:
    from .network import PublicResolver, RSSClient, clean, public_url
except ImportError:  # Standalone unit tests.
    from network import PublicResolver, RSSClient, clean, public_url

TAVILY_ENDPOINT = "https://api.tavily.com/search"
MAX_BYTES = 512 * 1024
MAX_SOURCES = 3


def _text(value, limit):
    return clean(value, limit) if isinstance(value, str) else ""


class SearchClient:
    def __init__(self, search_mode="tavily", tavily_api_key=""):
        self.search_mode = search_mode
        self._api_key = tavily_api_key.strip() if isinstance(tavily_api_key, str) else ""
        self.session = None
        self.rss = RSSClient()

    async def start(self):
        if self.search_mode == "rss":
            await self.rss.start()
            self.session = self.rss.session
        elif self.search_mode == "tavily" and (self.session is None or self.session.closed):
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=PublicResolver(), limit=3, ttl_dns_cache=0),
                timeout=aiohttp.ClientTimeout(total=12, connect=5),
                trust_env=False,
                headers={"User-Agent": "XiaodLife/0.2", "Accept": "application/json"},
            )

    async def close(self):
        await self.rss.close()
        if self.session is not None and not self.session.closed:
            await self.session.close()

    def _redact(self, text):
        return text.replace(self._api_key, "[REDACTED]") if self._api_key else text

    async def _tavily(self, query, maximum):
        await self.start()
        async with self.session.post(
            TAVILY_ENDPOINT,
            json={
                "query": query,
                "topic": "general",
                "search_depth": "basic",
                "max_results": maximum,
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
                "auto_parameters": False,
            },
            headers={"Authorization": "Bearer " + self._api_key},
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                # Never retain provider error bodies, request headers or exception messages.
                raise ValueError("search HTTP failure")
            if response.content_length and response.content_length > MAX_BYTES:
                raise ValueError("search response exceeds byte limit")
            body = bytearray()
            async for chunk in response.content.iter_chunked(16384):
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("search response exceeds byte limit")
        data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("invalid search response")
        retrieved = datetime.now(timezone.utc).isoformat()
        sources, seen = [], set()
        for row in data["results"]:
            if not isinstance(row, dict):
                continue
            try:
                url = public_url(row.get("url"))
            except (ValueError, TypeError):
                continue
            # Skip rather than alter source URLs if a provider unexpectedly echoes a secret.
            if self._api_key in url or url in seen:
                continue
            title = self._redact(_text(row.get("title"), 200))
            excerpt = self._redact(_text(row.get("content"), 1800))
            if not title or not excerpt:
                continue
            source = {
                "title": title,
                "url": url,
                "excerpt": excerpt,
                "retrieved_at": retrieved,
                "evidence_type": "search_snippet",
            }
            published = _text(row.get("published_date") or row.get("published_at"), 100)
            if published:
                source["published_at"] = self._redact(published)
            sources.append(source)
            seen.add(url)
            if len(sources) >= maximum:
                break
        return sources

    async def search(self, feeds, keywords, maximum):
        """Return (sources, typed errors); cancellation propagates normally."""
        try:
            maximum = min(MAX_SOURCES, max(0, int(maximum)))
            if maximum == 0:
                return [], []
            if self.search_mode == "rss":
                sources, errors = await self.rss.search(feeds, keywords, maximum)
                retrieved = datetime.now(timezone.utc).isoformat()
                return [dict(s, retrieved_at=retrieved) for s in sources[:maximum]], errors
            if self.search_mode != "tavily":
                return [], [{"provider": "search", "error_type": "InvalidSearchMode"}]
            if not self._api_key:
                return [], [{"provider": "tavily", "error_type": "MissingAPIKey"}]
            if isinstance(keywords, str):
                keywords = [keywords]
            query = " ".join(_text(word, 100) for word in list(keywords)[:20]).strip()[:500]
            if not query:
                return [], [{"provider": "tavily", "error_type": "EmptyQuery"}]
            # An outer deadline also bounds body streaming with a mocked/custom session.
            async with asyncio.timeout(15):
                return await self._tavily(query, maximum), []
        except Exception as exc:
            return [], [{"provider": "search", "error_type": type(exc).__name__}]
