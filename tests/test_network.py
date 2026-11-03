"""HTTP boundary tests; no external requests are made."""

import unittest
from unittest.mock import AsyncMock

from network import MAX_BYTES, RSSClient


class Content:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class Response:
    def __init__(self, status=200, length=None, chunks=()):
        self.status, self.content_length = status, length
        self.content = Content(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Session:
    closed = False

    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class HTTPBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_not_followed(self):
        client = RSSClient()
        client.session = Session(Response(status=302))
        with self.assertRaises(ValueError):
            await client.fetch("https://example.com/rss")
        self.assertFalse(client.session.calls[0][1]["allow_redirects"])

    async def test_declared_size_rejected(self):
        client = RSSClient()
        client.session = Session(Response(length=MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            await client.fetch("https://example.com/rss")

    async def test_stream_size_rejected_without_content_length(self):
        client = RSSClient()
        client.session = Session(Response(chunks=[b"x" * 16384] * (MAX_BYTES // 16384 + 1)))
        with self.assertRaises(ValueError):
            await client.fetch("https://example.com/rss")

    async def test_private_ip_rejected_before_session_start(self):
        client = RSSClient()
        client.start = AsyncMock()
        with self.assertRaises(ValueError):
            await client.fetch("https://169.254.169.254/latest/meta-data/")
        client.start.assert_not_called()
