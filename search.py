"""Bounded search snippets from Tavily, or explicitly selected public RSS.

API reference: https://docs.tavily.com/documentation/api-reference/endpoint/search
Extract fetches selected public pages through Tavily; generated answers are never evidence.
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import aiohttp

try:
    from .network import PublicResolver, RSSClient, clean, public_url
except ImportError:  # Standalone unit tests.
    from network import PublicResolver, RSSClient, clean, public_url

TAVILY_ENDPOINT = "https://api.tavily.com/search"
MAX_BYTES = 512 * 1024
MAX_SOURCES = 3
MAX_CANDIDATES = 12
EXTRACT_ENDPOINT = "https://api.tavily.com/extract"
PLATFORM_DOMAINS = {
    "xiaohongshu.com": "xiaohongshu",
    "bilibili.com": "bilibili",
    "zhihu.com": "zhihu",
}


def source_platform(url):
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return next(
        (
            name
            for domain, name in PLATFORM_DOMAINS.items()
            if host == domain or host.endswith("." + domain)
        ),
        "web",
    )


def validated_domains(domains):
    if domains is None:
        return []
    if not isinstance(domains, (list, tuple)) or len(domains) > 3:
        raise ValueError("invalid domain filters")
    if any(not isinstance(x, str) or x not in PLATFORM_DOMAINS for x in domains):
        raise ValueError("unsupported platform domain")
    return list(dict.fromkeys(domains))


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
                headers={"User-Agent": "XiaodLife/0.3", "Accept": "application/json"},
            )

    async def close(self):
        await self.rss.close()
        if self.session is not None and not self.session.closed:
            await self.session.close()

    def _redact(self, text):
        return text.replace(self._api_key, "[REDACTED]") if self._api_key else text

    def _safe_text(self, value, limit):
        # The HTTP response is already byte-bounded. Normalize its entire field
        # before redaction, then truncate: cutting first can retain a key prefix.
        return self._redact(_text(value, None))[:limit]

    async def _tavily(self, query, maximum, include_domains=None):
        domains = validated_domains(include_domains)
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
                "include_domains": domains,
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
            host = (urlsplit(url).hostname or "").lower().rstrip(".")
            if domains and not any(host == d or host.endswith("." + d) for d in domains):
                continue
            # Skip rather than alter source URLs if a provider unexpectedly echoes a secret.
            if (self._api_key and self._api_key in url) or url in seen:
                continue
            title = self._safe_text(row.get("title"), 200)
            excerpt = self._safe_text(row.get("content"), 1800)
            if not title or not excerpt:
                continue
            source = {
                "title": title,
                "url": url,
                "excerpt": excerpt,
                "retrieved_at": retrieved,
                "evidence_type": "search_snippet",
                "source_platform": source_platform(url),
            }
            published = self._safe_text(row.get("published_date") or row.get("published_at"), 100)
            if published:
                source["published_at"] = published
            sources.append(source)
            seen.add(url)
            if len(sources) >= maximum:
                break
        return sources

    async def search(self, feeds, keywords, maximum, include_domains=None, candidate_mode=False):
        """Return (sources, typed errors); cancellation propagates normally."""
        try:
            maximum = min(MAX_CANDIDATES if candidate_mode else MAX_SOURCES, max(0, int(maximum)))
            domains = validated_domains(include_domains)
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
                return await self._tavily(query, maximum, domains), []
        except Exception as exc:
            return [], [{"provider": "search", "error_type": type(exc).__name__}]

    async def discover_rss(self, feeds, maximum):
        """Read bounded public feeds without narrowing to existing interests."""
        sources, errors = await self.rss.discover(feeds, min(MAX_CANDIDATES, max(0, int(maximum))))
        retrieved = datetime.now(timezone.utc).isoformat()
        return [
            dict(s, source_platform=source_platform(s["url"]), retrieved_at=retrieved)
            for s in sources
        ], errors

    async def _extract_request(self, urls):
        # RSS mode normally owns an RSS session: do not send credentials through it.
        # A separate fixed-endpoint session is needed only when extraction is enabled.
        if self.search_mode == "rss":
            session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=PublicResolver(), limit=2, ttl_dns_cache=0),
                timeout=aiohttp.ClientTimeout(total=12, connect=5),
                trust_env=False,
            )
        else:
            await self.start()
            session = self.session
        try:
            async with session.post(
                EXTRACT_ENDPOINT,
                json={
                    "urls": urls,
                    "extract_depth": "basic",
                    "format": "text",
                    "timeout": 10,
                    "include_images": False,
                    "include_usage": True,
                },
                headers={"Authorization": "Bearer " + self._api_key},
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise ValueError("extract HTTP failure")
                if response.content_length and response.content_length > MAX_BYTES:
                    raise ValueError("extract response exceeds byte limit")
                body = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ValueError("extract response exceeds byte limit")
            data = json.loads(body)
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError("invalid extract response")
            return data
        finally:
            if self.search_mode == "rss":
                await session.close()

    async def extract(self, sources, maximum=2, max_chars=6000):
        """One bounded batch; partial/failed pages retain explicitly labelled snippets.

        Only exact requested URLs can contribute evidence. Redirected/canonicalized
        URLs are conservatively treated as failures rather than silently reassigned.
        """
        maximum = max(0, min(2, int(maximum)))
        max_chars = max(500, min(12000, int(max_chars)))
        selected, errors, seen = [], [], set()
        for source in sources[:maximum]:
            try:
                url = public_url(source.get("url"))
                if (self._api_key and self._api_key in url) or url in seen:
                    raise ValueError("invalid or duplicate extraction URL")
                selected.append(
                    dict(
                        source,
                        read_level="summary",
                        read_content=self._safe_text(source.get("excerpt"), 1800),
                        source_platform=source_platform(url),
                    )
                )
                seen.add(url)
            except (ValueError, TypeError, AttributeError):
                errors.append({"provider": "extract", "error_type": "InvalidURL"})
        if not selected:
            return selected, errors
        if not self._api_key:
            errors.append({"provider": "extract", "error_type": "MissingAPIKey"})
            for source in selected:
                source["extract_error_type"] = "MissingAPIKey"
            return selected, errors
        try:
            async with asyncio.timeout(15):
                data = await self._extract_request([s["url"] for s in selected])
            evidence = {}
            for row in data["results"]:
                if not isinstance(row, dict) or row.get("url") not in seen:
                    continue
                raw = row.get("raw_content")
                if isinstance(raw, str):
                    raw = re.sub(
                        r"<(script|style)\b[^>]*>.*?</\1\s*>",
                        " ",
                        raw,
                        flags=re.IGNORECASE | re.DOTALL,
                    )
                text = self._safe_text(raw, max_chars)
                if text:
                    evidence.setdefault(row["url"], text)
            retrieved = datetime.now(timezone.utc).isoformat()
            for source in selected:
                if source["url"] in evidence:
                    source.update(
                        read_level="full_text",
                        read_content=evidence[source["url"]],
                        extracted_at=retrieved,
                    )
                else:
                    source["extract_error_type"] = "NoExtractedContent"
                    errors.append(
                        {
                            "provider": "extract",
                            "url": source["url"],
                            "error_type": "NoExtractedContent",
                        }
                    )
        except Exception as exc:
            error_type = type(exc).__name__
            errors.append({"provider": "extract", "error_type": error_type})
            for source in selected:
                source["extract_error_type"] = error_type
        return selected, errors
