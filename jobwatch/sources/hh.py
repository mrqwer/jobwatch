import asyncio
import logging
import os

import httpx

from .. import db
from ..db import Vacancy
from ..scoring import score, strip_html

NAME = "hh"
API = "https://api.hh.ru"
log = logging.getLogger(NAME)
_token = None


def enabled(cfg: dict) -> bool:
    # anonymous api.hh.ru/vacancies answers 403; needs an app registered at dev.hh.ru
    return bool(os.environ.get("HH_CLIENT_ID") and os.environ.get("HH_CLIENT_SECRET") and cfg.get("hh", {}).get("texts"))


def _token_file() -> str:
    return os.path.join(os.path.dirname(db.DB_PATH) or ".", "hh_token")


def _forget():
    """hh rejected the token: drop it so the next cycle asks for a new one."""
    global _token
    _token = None
    if os.path.exists(_token_file()):
        os.remove(_token_file())


async def _auth(http: httpx.AsyncClient) -> str:
    # The application token does not expire, and hh answers 403 to a new /token request made within
    # a few minutes of the previous one. Asking on every process start broke restarts: keep it on disk.
    global _token
    if not _token and os.path.exists(_token_file()):
        with open(_token_file()) as f:
            _token = f.read().strip() or None
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
        with open(_token_file(), "w") as f:
            f.write(_token)
    return _token


def _salary(s: dict | None) -> str:
    if not s:
        return ""
    return f"{s.get('from') or ''}-{s.get('to') or ''} {s.get('currency') or ''}".strip()


async def fetch(cfg: dict) -> list[Vacancy]:
    c = cfg["hh"]
    ua = os.environ.get("HH_USER_AGENT", "jobwatch/0.1 (set-your-email@example.com)")
    found: dict[str, Vacancy] = {}  # one vacancy often matches several search texts
    async with httpx.AsyncClient(headers={"User-Agent": ua, "HH-User-Agent": ua}, timeout=20) as http:
        headers = {"Authorization": f"Bearer {await _auth(http)}"}
        for text in c["texts"]:
            for j in await _search(http, headers, text, c):
                sn = j.get("snippet") or {}
                found[str(j["id"])] = Vacancy(
                    source=NAME,
                    ext_id=str(j["id"]),
                    title=j["name"],
                    company=(j.get("employer") or {}).get("name", ""),
                    url=j["alternate_url"],
                    salary=_salary(j.get("salary")),
                    text=strip_html(f"{sn.get('requirement') or ''} {sn.get('responsibility') or ''}"),
                )

        # Search returns a ~300-char snippet, too little to score ("Python-разработчик" stayed at 4 < 6).
        # Full description + key skills for NEW vacancies that already look half-relevant, like Habr.
        half = cfg["threshold"] / 2
        for v in found.values():
            if db.seen(NAME, v.ext_id) or score(v.title, v.text, cfg["keywords"]) < half:
                continue
            v.text += ". " + await details(http, headers, v.ext_id)
            await asyncio.sleep(0.5)
    return list(found.values())


async def _search(http: httpx.AsyncClient, headers: dict, text: str, c: dict) -> list[dict]:
    """All pages: one page was 100 of 312 for "ai engineer". hh serves at most 2000 (20 pages)."""
    items, page, pages = [], 0, 1
    while page < min(pages, 20):
        params = {
            "text": text,
            "area": c.get("areas", []),  # 40 = Kazakhstan, 113 = Russia, 1 = Moscow ...
            "order_by": "publication_time",
            "period": c.get("period_days", 3),
            "per_page": 100,
            "page": page,
        }
        r = await http.get(f"{API}/vacancies", params=params, headers=headers)
        if r.status_code in (401, 403):
            _forget()  # token revoked -> re-auth on the next cycle
        r.raise_for_status()
        data = r.json()
        items += data["items"]
        pages, page = data["pages"], page + 1
        await asyncio.sleep(1)
    return items


async def details(http: httpx.AsyncClient, headers: dict, vid: str) -> str:
    r = await http.get(f"{API}/vacancies/{vid}", headers=headers)
    if r.status_code != 200:
        log.warning("hh vacancy %s: HTTP %s", vid, r.status_code)
        return ""
    d = r.json()
    skills = ", ".join(s["name"] for s in d.get("key_skills") or [])
    return f"{skills}. {strip_html(d.get('description', ''))}"
