import asyncio
import logging

import httpx

from .. import db
from ..config import UA
from ..db import Vacancy
from ..scoring import score, strip_html
from .boards import job_posting

NAME = "habr"
BASE = "https://career.habr.com"
LIST_URL = BASE + "/api/frontend/vacancies"
log = logging.getLogger(NAME)


def enabled(cfg: dict) -> bool:
    return bool(cfg.get("habr_queries"))


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
            # the list endpoint has no description; the vacancy page has it
            v.text += ". " + strip_html((await job_posting(http, v.url)).get("description", ""))
            await asyncio.sleep(1)
    return list(found.values())
