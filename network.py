"""Bounded public RSS/Atom retrieval. No browser, credentials or arbitrary tools."""

import asyncio
import ipaddress
import re
import socket
from html import unescape
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.resolver import DefaultResolver
from defusedxml import ElementTree

MAX_BYTES = 512 * 1024
MAX_FEEDS = 3
MAX_ENTRIES = 40


def public_url(url: str) -> str:
    """Reject local addresses, credentials, non-HTTPS and nonstandard ports."""
    if not isinstance(url, str) or len(url) > 1024:
        raise ValueError("invalid URL")
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise ValueError("only public HTTPS URLs without credentials are allowed")
    if p.port not in (None, 443) or p.fragment:
        raise ValueError("unsupported URL port or fragment")
    host = p.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("local hostname is forbidden")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError("single-label host is forbidden") from None
    else:
        if not ip.is_global:
            raise ValueError("non-public IP is forbidden")
    return url


class PublicResolver(DefaultResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        results = await super().resolve(host, port, family)
        if not results or any(not ipaddress.ip_address(r["host"]).is_global for r in results):
            raise OSError("DNS resolved to a non-public address")
        return results


def clean(text, limit=1800):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", " ", text or ""))).strip()[:limit]


def parse_feed(body: bytes, feed_url: str) -> list[dict]:
    root = ElementTree.fromstring(body)
    entries = []
    for node in root.iter():
        if node.tag.split("}")[-1] not in ("item", "entry"):
            continue
        fields = {}
        links = []
        for child in node:
            name = child.tag.split("}")[-1]
            if name == "link":
                if child.attrib.get("rel", "alternate") == "alternate":
                    links.append(child.attrib.get("href") or child.text or "")
            elif name in (
                "title",
                "description",
                "summary",
                "content",
                "encoded",
                "published",
                "pubDate",
                "updated",
            ):
                fields[name] = "".join(child.itertext())
        if not links:
            continue
        try:
            url = public_url(urljoin(feed_url, links[0].strip()))
        except ValueError:
            continue
        title = clean(fields.get("title"), 200)
        excerpt = clean(
            fields.get("encoded")
            or fields.get("content")
            or fields.get("summary")
            or fields.get("description")
        )
        if not title or not excerpt:
            continue
        entries.append(
            {
                "url": url,
                "title": title,
                "excerpt": excerpt,
                "feed_url": feed_url,
                "published_at": clean(
                    fields.get("published") or fields.get("pubDate") or fields.get("updated"), 100
                ),
                "evidence_type": "rss_excerpt",
            }
        )
        if len(entries) >= MAX_ENTRIES:
            break
    return entries


class RSSClient:
    def __init__(self):
        self.session = None

    async def start(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=PublicResolver(), limit=3, ttl_dns_cache=0),
                timeout=aiohttp.ClientTimeout(total=12, connect=5),
                trust_env=False,
                headers={
                    "User-Agent": "XiaodLife/0.1 (RSS reader)",
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
                },
            )

    async def close(self):
        if self.session:
            await self.session.close()

    async def fetch(self, url):
        public_url(url)
        await self.start()
        # No redirects: a feed operator cannot redirect this client into local services.
        async with self.session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}")
            if response.content_length and response.content_length > MAX_BYTES:
                raise ValueError("feed exceeds byte limit")
            body = bytearray()
            async for chunk in response.content.iter_chunked(16384):
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("feed exceeds byte limit")
            return await asyncio.to_thread(parse_feed, bytes(body), url)

    async def search(self, feeds, keywords, maximum):
        feeds = list(dict.fromkeys(feeds))[:MAX_FEEDS]
        results = await asyncio.gather(*(self.fetch(url) for url in feeds), return_exceptions=True)
        candidates, errors, seen = [], [], set()
        for url, result in zip(feeds, results):
            if isinstance(result, BaseException):
                errors.append({"feed_url": url, "error_type": type(result).__name__})
                continue
            for entry in result:
                text = (entry["title"] + " " + entry["excerpt"]).casefold()
                score = sum(1 for word in keywords if word.casefold() in text)
                if score and entry["url"] not in seen:
                    seen.add(entry["url"])
                    candidates.append((score, entry))
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in candidates[:maximum]], errors
