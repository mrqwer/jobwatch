import asyncio

import httpx

from jobwatch.sources import hh


def test_search_reads_all_pages(monkeypatch):
    def handler(req: httpx.Request):
        page = int(req.url.params["page"])
        return httpx.Response(200, json={"pages": 3, "items": [{"id": page}]})

    real_sleep = asyncio.sleep
    monkeypatch.setattr(hh.asyncio, "sleep", lambda s: real_sleep(0))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    items = asyncio.run(hh._search(http, {}, "python", {}))
    assert [i["id"] for i in items] == [0, 1, 2]
