import asyncio
import json
import logging
import re

import httpx

from .. import db
from ..config import UA
from ..db import Vacancy
from ..scoring import score, strip_html

NAME = "habr"
BASE = "https://career.habr.com"
LIST_URL = BASE + "/api/frontend/vacancies"
LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
log = logging.getLogger(NAME)


def enabled(cfg: dict) -> bool:
    return bool(cfg.get("habr_queries"))


async def _description(http: httpx.AsyncClient, v: Vacancy) -> str:
    """The list endpoint has no description; the vacancy page carries it in JSON-LD (JobPosting)."""
    try:
        r = await http.get(v.url)
        r.raise_for_status()
        for m in LD_JSON.finditer(r.text):
            d = json.loads(m.group(1))
            if d.get("@type") == "JobPosting":
                return strip_html(d.get("description", ""))
    except (httpx.HTTPError, ValueError):
        log.exception("description of %s failed", v.url)
    return ""


async def fetch(cfg: dict) -> list[Vacancy]:
    found: dict[str, Vacancy] = {}  # one vacancy often matches several queries
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20, follow_redirects=True) as http:
        for q in cfg["habr_queries"]:
            for page in (1, 2):
                try:
                    r = await http.get(LIST_URL, params={"q": q, "type": "all", "sort": "date", "page": page})
                    r.raise_for_status()
                except httpx.HTTPError:
                    log.exception("query %r page %s failed", q, page)
                    break
                for j in r.json()["list"]:
                    extra = [s["title"] for s in j.get("skills") or []]
                    extra += [d["title"] for d in j.get("divisions") or []]
                    extra += [loc["title"] for loc in j.get("locations") or []]
                    if j.get("remoteWork"):
                        extra.append("remote")
                    if j.get("salaryQualification"):
                        extra.append(j["salaryQualification"]["title"])
                    found[str(j["id"])] = Vacancy(
                        source=NAME,
                        ext_id=str(j["id"]),
                        title=j["title"],
                        company=(j.get("company") or {}).get("title", ""),
                        url=BASE + j["href"],
                        salary=(j.get("salary") or {}).get("formatted", ""),
                        text=", ".join(extra),
                    )
                await asyncio.sleep(1)  # be polite

        # Descriptions only for NEW vacancies that already look at least half-relevant by title + skills:
        # keeps requests low, and the description can still lift (or sink) the final score.
        half = cfg["threshold"] / 2
        for v in found.values():
            if db.seen(NAME, v.ext_id) or score(v.title, v.text, cfg["keywords"]) < half:
                continue
            v.text += ". " + await _description(http, v)
            await asyncio.sleep(1)
    return list(found.values())
