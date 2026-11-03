"""Search boundary tests use fake HTTP only; never spend provider credits."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from search import MAX_BYTES, TAVILY_ENDPOINT, SearchClient


class Content:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class Response:
    def __init__(self, data=None, status=200, length=None, chunks=None):
        self.status, self.content_length = status, length
        self.content = Content(chunks if chunks is not None else [json.dumps(data).encode()])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Session:
    closed = False

    def __init__(self, response):
        self.response, self.calls = response, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    async def close(self):
        self.closed = True


def row(url="https://example.com/story", **kwargs):
    return {"url": url, "title": "<b>News</b>", "content": "A snippet", **kwargs}


class SearchTests(unittest.IsolatedAsyncioTestCase):
    def client(self, response):
        client = SearchClient(tavily_api_key="tvly-secret")
        client.session = Session(response)
        return client

    async def test_default_missing_key_no_network_or_fallback(self):
        client = SearchClient()
        client.start = AsyncMock()
        client.rss.search = AsyncMock()
        sources, errors = await client.search([], ["news"], 3)
        self.assertEqual(sources, [])
        self.assertEqual(errors[0]["error_type"], "MissingAPIKey")
        client.start.assert_not_called()
        client.rss.search.assert_not_called()

    async def test_fixed_request_and_evidence(self):
        client = self.client(Response({"results": [row(published_date="2026-01-01")]}))
        sources, errors = await client.search(["ignored"], ["AI", "research"], 99)
        self.assertFalse(errors)
        self.assertEqual(sources[0]["title"], "News")
        self.assertEqual(sources[0]["published_at"], "2026-01-01")
        self.assertEqual(sources[0]["evidence_type"], "search_snippet")
        self.assertTrue(sources[0]["retrieved_at"])
        url, options = client.session.calls[0]
        self.assertEqual(url, TAVILY_ENDPOINT)
        self.assertFalse(options["allow_redirects"])
        self.assertEqual(options["headers"]["Authorization"], "Bearer tvly-secret")
        self.assertEqual(options["json"]["query"], "AI research")
        self.assertEqual(options["json"]["max_results"], 3)
        for key in ("include_answer", "include_raw_content", "include_images", "auto_parameters"):
            self.assertFalse(options["json"][key])

    async def test_filter_urls_duplicates_missing_content_and_cap(self):
        rows = [
            row(u)
            for u in [
                "http://example.com",
                "https://127.0.0.1/",
                "https://a.local/",
                "https://u:p@example.com/",
                "https://example.com:123/",
                "https://example.com/tvly-secret",
            ]
        ]
        rows += [None, {"url": "https://example.com/"}, row(), row()]
        rows += [row(f"https://example.com/{n}") for n in range(5)]
        client = self.client(Response({"results": rows, "answer": "fabricated"}))
        sources, errors = await client.search([], ["news"], 100)
        self.assertEqual(len(sources), 3)
        self.assertEqual(len({s["url"] for s in sources}), 3)
        self.assertFalse(errors)
        self.assertNotIn("published_at", sources[0])

    async def test_redirect_and_error_body_not_exposed(self):
        for status in (301, 401, 429, 500):
            client = self.client(Response({"error": "tvly-secret"}, status=status))
            sources, errors = await client.search([], ["news"], 3)
            self.assertFalse(sources)
            self.assertEqual(errors, [{"provider": "search", "error_type": "ValueError"}])
            self.assertNotIn("tvly-secret", str(errors))

    async def test_declared_and_streamed_byte_limits(self):
        for response in (Response(length=MAX_BYTES + 1), Response(chunks=[b"x" * MAX_BYTES, b"x"])):
            sources, errors = await self.client(response).search([], ["news"], 3)
            self.assertFalse(sources)
            self.assertEqual(errors[0]["error_type"], "ValueError")

    async def test_malformed_json_and_schema(self):
        for response in (Response(chunks=[b"{"]), Response([]), Response({"results": {}})):
            sources, errors = await self.client(response).search([], ["news"], 3)
            self.assertFalse(sources)
            self.assertTrue(errors)

    async def test_provider_echo_redacted_and_raw_content_ignored(self):
        client = self.client(
            Response({"results": [row(content="tvly-secret", raw_content="FULL")]})
        )
        sources, _ = await client.search([], ["news"], 3)
        self.assertEqual(sources[0]["excerpt"], "[REDACTED]")
        self.assertNotIn("FULL", str(sources))
        self.assertNotIn("tvly-secret", str(sources))

    async def test_timeout_error_and_cancellation(self):
        client = self.client(Response({}))
        client._tavily = AsyncMock(side_effect=TimeoutError("tvly-secret"))
        _, errors = await client.search([], ["news"], 3)
        self.assertEqual(errors[0]["error_type"], "TimeoutError")
        client._tavily = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await client.search([], ["news"], 3)

    async def test_explicit_rss(self):
        client = SearchClient(search_mode="rss")
        client.rss.search = AsyncMock(return_value=([row(evidence_type="rss_excerpt")], []))
        sources, errors = await client.search(["https://example.com/rss"], ["news"], 9)
        client.rss.search.assert_awaited_once_with(["https://example.com/rss"], ["news"], 3)
        self.assertTrue(sources[0]["retrieved_at"])
        self.assertFalse(errors)

    async def test_empty_query_zero_maximum_and_invalid_mode(self):
        client = self.client(Response({}))
        for keywords, maximum in (([], 3), (["news"], 0)):
            sources, _ = await client.search([], keywords, maximum)
            self.assertFalse(sources)
        self.assertFalse(client.session.calls)
        _, errors = await SearchClient("unknown").search([], ["news"], 3)
        self.assertEqual(errors[0]["error_type"], "InvalidSearchMode")

    async def test_real_session_security_settings_and_lifecycle(self):
        client = SearchClient(tavily_api_key="tvly-secret")
        await client.start()
        session = client.session
        self.assertFalse(session.trust_env)
        self.assertEqual(session.timeout.total, 12)
        self.assertEqual(session.timeout.connect, 5)
        self.assertNotIn("Authorization", session.headers)
        await client.start()
        self.assertIs(session, client.session)
        await client.close()
        self.assertTrue(session.closed)
