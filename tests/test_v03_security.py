"""Independent hostile-provider and budget regression tests (fake network only)."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from test_search import Response, Session

from search import SearchClient


class ExtractSecurityTests(unittest.IsolatedAsyncioTestCase):
    def client(self, payload):
        client = SearchClient(tavily_api_key="tvly-review-secret")
        client.session = Session(Response(payload))
        return client

    def source(self, url="https://example.com/article"):
        return {
            "id": 1,
            "url": url,
            "title": "Article",
            "excerpt": "Trusted-as-data snippet",
            "evidence_type": "search_snippet",
        }

    async def test_unrequested_provider_result_never_becomes_read_source(self):
        client = self.client(
            {
                "results": [
                    {"url": "https://attacker.example/other", "raw_content": "Unrequested body"}
                ]
            }
        )
        sources, _ = await client.extract([self.source()], 2, 6000)
        self.assertNotIn("Unrequested body", json.dumps(sources))
        self.assertNotIn("https://attacker.example", json.dumps(sources))

    async def test_private_source_does_not_reach_extract_service(self):
        for url in (
            "https://127.0.0.1/",
            "https://169.254.169.254/",
            "https://foo.internal/",
            "https://user:password@example.com/",
        ):
            client = self.client({"results": []})
            await client.extract([self.source(url)], 2, 6000)
            self.assertFalse(client.session.calls, url)

    async def test_extract_provider_error_does_not_leak_secret(self):
        client = self.client({"error": "tvly-review-secret"})
        client.session = Session(Response({"error": "tvly-review-secret"}, status=500))
        sources, errors = await client.extract([self.source()], 2, 6000)
        self.assertNotIn("tvly-review-secret", json.dumps([sources, errors]))
        self.assertLessEqual(
            len(client.session.calls), 1, "Unexpected hidden retries can exceed budget"
        )

    async def test_extract_request_is_fixed_endpoint_and_no_redirects(self):
        client = self.client(
            {"results": [{"url": "https://example.com/article", "raw_content": "Body " * 100}]}
        )
        await client.extract([self.source()], 2, 6000)
        self.assertEqual(len(client.session.calls), 1)
        url, options = client.session.calls[0]
        self.assertEqual(url, "https://api.tavily.com/extract")
        self.assertFalse(options["allow_redirects"])
        self.assertEqual(options["headers"]["Authorization"], "Bearer tvly-review-secret")
        self.assertEqual(options["json"]["urls"], ["https://example.com/article"])

    async def test_extract_response_redacts_key(self):
        client = self.client(
            {
                "results": [
                    {
                        "url": "https://example.com/article",
                        "raw_content": "Body tvly-review-secret " * 100,
                    }
                ]
            }
        )
        sources, errors = await client.extract([self.source()], 2, 6000)
        self.assertNotIn("tvly-review-secret", json.dumps([sources, errors]))

    async def test_extract_cancellation_propagates(self):
        client = self.client({})
        client.start = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await client.extract([self.source()], 2, 6000)

    async def test_redaction_happens_before_body_truncation(self):
        body = "x" * 490 + "tvly-review-secret" + " tail"
        client = self.client(
            {"results": [{"url": "https://example.com/article", "raw_content": body}]}
        )
        sources, _ = await client.extract([self.source()], 2, 500)
        self.assertNotIn("tvly-review", sources[0]["read_content"])
        self.assertNotIn("tvly-", sources[0]["read_content"])

    async def test_html_encoded_key_is_redacted_after_normalization(self):
        encoded = "tvly&#45;review&#45;secret"
        client = self.client(
            {"results": [{"url": "https://example.com/article", "raw_content": encoded}]}
        )
        sources, errors = await client.extract([self.source()], 2, 6000)
        self.assertNotIn("tvly-review-secret", json.dumps([sources, errors]))

    async def test_redaction_happens_before_search_truncation(self):
        client = self.client(
            {
                "results": [
                    {
                        "url": "https://example.com/article",
                        "title": "x" * 190 + "tvly-review-secret",
                        "content": "snippet",
                    }
                ]
            }
        )
        sources, _ = await client.search([], ["query"], 2)
        self.assertNotIn("tvly-", sources[0]["title"])


class CoreBudgetSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, *, selection=None, search_error=None, max_extract_pages=2):
        import tempfile

        from core import Explorer, Store

        source = {"url": "https://example.com/article", "title": "Article", "excerpt": "snippet"}
        plan = {"exploration_mode": "active_search", "topic": "nature", "keywords": ["nature"]}
        selection = (
            selection if selection is not None else [{"source_id": 1, "reason": "curiosity"}]
        )
        llm = AsyncMock(
            side_effect=[
                json.dumps(plan),
                json.dumps({"selected": selection}),
                RuntimeError("private exception detail"),
            ]
        )
        search = AsyncMock(return_value=([source], []), side_effect=search_error)
        extract = AsyncMock(
            return_value=([dict(source, read_level="full_text", read_content="body")], [])
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            record, _ = await Explorer(
                store, search, llm, extract=extract, max_extract_pages=max_extract_pages
            ).run()
            self.assertEqual(len(store.read("activities.json", [])), 1)
        return record, llm, search, extract

    async def test_failed_search_counted_without_retry(self):
        record, llm, search, extract = await self.run_case(
            search_error=TimeoutError("private error")
        )
        self.assertEqual(record["usage"]["search_calls"], 1)
        self.assertEqual(search.await_count, 1)
        self.assertEqual(llm.await_count, 1)
        extract.assert_not_awaited()
        self.assertNotIn("private error", json.dumps(record))

    async def test_failed_read_counts_attempt_preserves_real_evidence(self):
        record, llm, search, extract = await self.run_case()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["usage"], {"search_calls": 1, "extract_pages": 1, "llm_calls": 3})
        self.assertEqual(record["sources"][0]["read_content"], "body")
        self.assertEqual(llm.await_count, 3)
        self.assertNotIn("private exception detail", json.dumps(record))

    async def test_boolean_or_duplicate_selection_rejected_before_network(self):
        for selection in (
            [{"source_id": True, "reason": "reason"}],
            [{"source_id": 1, "reason": "one"}, {"source_id": 1, "reason": "two"}],
        ):
            record, llm, search, extract = await self.run_case(selection=selection)
            self.assertEqual(record["status"], "failed")
            extract.assert_not_awaited()
            self.assertEqual(record["sources"], [])
            self.assertEqual(llm.await_count, 2)


class InitializationBudgetTests(unittest.IsolatedAsyncioTestCase):
    from test_adapter import AdapterTests

    asyncSetUp = AdapterTests.asyncSetUp
    asyncTearDown = AdapterTests.asyncTearDown

    async def test_invalid_budget_rejected_before_session_start(self):
        self.plugin.config["max_llm_calls"] = 4
        self.plugin.client.start = AsyncMock()
        with self.assertRaises(ValueError):
            await self.plugin.initialize()
        self.plugin.client.start.assert_not_awaited()
        self.assertIsNone(self.plugin.engine)

    async def test_invalid_domain_rejected_before_session_start(self):
        self.plugin.config["include_domains"] = ["127.0.0.1"]
        self.plugin.client.start = AsyncMock()
        with self.assertRaises(ValueError):
            await self.plugin.initialize()
        self.plugin.client.start.assert_not_awaited()

    async def test_production_adapter_always_selects_v03_engine(self):
        self.plugin.config["auto_enabled"] = False
        self.plugin.client.start = AsyncMock()
        await self.plugin.initialize()
        self.assertTrue(self.plugin.engine.v03)
        self.assertEqual(self.plugin.engine.extract, self.plugin.client.extract)
        self.assertEqual(self.plugin.engine.discover, self.plugin.client.discover_rss)
        self.assertEqual(self.plugin.engine.max_search_calls, 2)
        self.assertEqual(self.plugin.engine.max_extract_pages, 2)
        self.assertEqual(self.plugin.engine.max_llm_calls, 3)
