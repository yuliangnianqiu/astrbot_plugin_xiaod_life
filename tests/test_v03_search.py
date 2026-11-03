"""V0.3 discovery and extraction boundaries, mocked HTTP only."""

import asyncio
import unittest
from unittest.mock import AsyncMock

from test_search import Response, Session, row

from network import RSSClient
from search import EXTRACT_ENDPOINT, MAX_BYTES, SearchClient, source_platform


class V03SearchTests(unittest.IsolatedAsyncioTestCase):
    def client(self, data=None, response=None):
        client = SearchClient(tavily_api_key="tvly-secret")
        client.session = Session(response or Response(data))
        return client

    async def test_domains_and_platforms(self):
        domains = ["xiaohongshu.com", "bilibili.com", "zhihu.com"]
        client = self.client({"results": [row("https://www." + d + "/page") for d in domains]})
        sources, errors = await client.search(
            [], ["music"], 12, include_domains=domains, candidate_mode=True
        )
        self.assertFalse(errors)
        self.assertEqual(
            [s["source_platform"] for s in sources], ["xiaohongshu", "bilibili", "zhihu"]
        )
        request = client.session.calls[0][1]["json"]
        self.assertEqual(request["include_domains"], domains)
        self.assertEqual(request["max_results"], 12)
        self.assertEqual(source_platform("https://zhihu.com.attacker.com/a"), "web")
        _, errors = await client.search([], ["x"], 3, include_domains=["localhost"])
        self.assertTrue(errors)
        self.assertEqual(len(client.session.calls), 1)

    async def test_extraction_partial_success_and_actual_content(self):
        selected = [row(), row("https://www.zhihu.com/a")]
        client = self.client(
            {
                "results": [
                    {"url": selected[0]["url"], "raw_content": "<p>Real body tvly-secret</p>"}
                ],
                "failed_results": [{"url": selected[1]["url"], "error": "secret body"}],
            }
        )
        sources, errors = await client.extract(selected)
        self.assertEqual([s["read_level"] for s in sources], ["full_text", "summary"])
        self.assertEqual(sources[0]["read_content"], "Real body [REDACTED]")
        self.assertEqual(sources[1]["url"], selected[1]["url"])
        self.assertNotIn("secret body", str(errors))
        endpoint, options = client.session.calls[0]
        self.assertEqual(endpoint, EXTRACT_ENDPOINT)
        self.assertEqual(options["json"]["urls"], [s["url"] for s in selected])
        self.assertFalse(options["allow_redirects"])
        self.assertNotIn("query", options["json"])

    async def test_extract_unrequested_redirect_and_empty_results(self):
        for results in (
            [],
            [{"url": "https://different.com/a", "raw_content": "UNRELATED"}],
            [{"url": row()["url"], "raw_content": "   "}],
        ):
            client = self.client({"results": results})
            sources, errors = await client.extract([dict(row(), excerpt="Summary")])
            self.assertEqual(sources[0]["read_level"], "summary")
            self.assertEqual(sources[0]["read_content"], "Summary")
            self.assertTrue(errors)

    async def test_extract_caps_and_byte_failure(self):
        selected = [dict(row("https://example.com/" + str(i)), excerpt="Summary") for i in range(8)]
        client = self.client({"results": [{"url": selected[0]["url"], "raw_content": "x" * 20000}]})
        sources, _ = await client.extract(selected, maximum=99, max_chars=99999)
        self.assertEqual(len(sources), 2)
        self.assertEqual(len(sources[0]["read_content"]), 12000)
        for response in (
            Response(status=429),
            Response(length=MAX_BYTES + 1),
            Response(chunks=[b"x" * MAX_BYTES, b"x"]),
            Response([]),
        ):
            client = self.client(response=response)
            sources, errors = await client.extract(selected)
            self.assertTrue(all(s["read_level"] == "summary" for s in sources))
            self.assertTrue(errors)

    async def test_extract_no_key_or_unsafe_url_never_calls(self):
        client = SearchClient()
        client._extract_request = AsyncMock()
        sources, errors = await client.extract([dict(row(), excerpt="Summary")])
        self.assertEqual(sources[0]["read_level"], "summary")
        self.assertEqual(errors[0]["error_type"], "MissingAPIKey")
        client._extract_request.assert_not_called()
        client = self.client({"results": []})
        sources, errors = await client.extract([row("https://127.0.0.1/a")])
        self.assertFalse(sources)
        self.assertTrue(errors)
        self.assertFalse(client.session.calls)

    async def test_extract_cancellation_propagates(self):
        client = self.client({})
        client._extract_request = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await client.extract([row()])

    async def test_rss_discovery_round_robin_without_keywords(self):
        client = RSSClient()
        client.fetch = AsyncMock(
            side_effect=[
                [row("https://example.com/a"), row("https://example.com/b")],
                [row("https://other.com/a"), row("https://other.com/b")],
            ]
        )
        results, errors = await client.discover(
            ["https://example.com/feed", "https://other.com/feed"], 3
        )
        self.assertFalse(errors)
        self.assertEqual(
            [s["url"] for s in results],
            ["https://example.com/a", "https://other.com/a", "https://example.com/b"],
        )

    async def test_rss_discovery_partial_failure_and_dedup(self):
        client = RSSClient()
        client.fetch = AsyncMock(side_effect=[ValueError("private text"), [row(), row()]])
        results, errors = await client.discover(
            ["https://bad.com/feed", "https://example.com/feed"], 12
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(errors[0]["error_type"], "ValueError")
        self.assertNotIn("private text", str(errors))
