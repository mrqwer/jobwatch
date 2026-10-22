import asyncio
import logging
import os

import httpx

from ..db import Vacancy
from ..scoring import strip_html

NAME = "hh"
API = "https://api.hh.ru"
log = logging.getLogger(NAME)
_token = None


def enabled(cfg: dict) -> bool:
    # anonymous api.hh.ru/vacancies answers 403; needs an app registered at dev.hh.ru
    return bool(os.environ.get("HH_CLIENT_ID") and os.environ.get("HH_CLIENT_SECRET") and cfg.get("hh", {}).get("texts"))


async def _auth(http: httpx.AsyncClient) -> str:
    global _token
    if not _token:
        r = await http.post(
            f"{API}/token",
            data={
                "grant_type": "client_credentials",
                "client_id": os.environ["HH_CLIENT_ID"],
                "client_secret": os.environ["HH_CLIENT_SECRET"],
            },
        )
        r.raise_for_status()
        _token = r.json()["access_token"]
    return _token


def _salary(s: dict | None) -> str:
    if not s:
        return ""
    return f"{s.get('from') or ''}-{s.get('to') or ''} {s.get('currency') or ''}".strip()


async def fetch(cfg: dict) -> list[Vacancy]:
    global _token
    c = cfg["hh"]
    ua = os.environ.get("HH_USER_AGENT", "jobwatch/0.1 (set-your-email@example.com)")
    out = []
    async with httpx.AsyncClient(headers={"User-Agent": ua, "HH-User-Agent": ua}, timeout=20) as http:
        headers = {"Authorization": f"Bearer {await _auth(http)}"}
        for text in c["texts"]:
            params = {
                "text": text,
                "area": c.get("areas", []),  # 40 = Kazakhstan, 113 = Russia, 1 = Moscow ...
                "order_by": "publication_time",
                "period": c.get("period_days", 3),
                "per_page": 100,
            }
            r = await http.get(f"{API}/vacancies", params=params, headers=headers)
            if r.status_code in (401, 403):
                _token = None  # token expired -> re-auth on the next cycle
            r.raise_for_status()
            for j in r.json()["items"]:
                sn = j.get("snippet") or {}
                out.append(
                    Vacancy(
                        source=NAME,
                        ext_id=str(j["id"]),
                        title=j["name"],
                        company=(j.get("employer") or {}).get("name", ""),
                        url=j["alternate_url"],
                        salary=_salary(j.get("salary")),
                        text=strip_html(f"{sn.get('requirement') or ''} {sn.get('responsibility') or ''}"),
                    )
                )
            await asyncio.sleep(1)
    return out
