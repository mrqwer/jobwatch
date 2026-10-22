import asyncio
import logging

import httpx

from ..config import UA
from ..db import Vacancy

NAME = "habr"
URL = "https://career.habr.com/api/frontend/vacancies"
log = logging.getLogger(NAME)


def enabled(cfg: dict) -> bool:
    return bool(cfg.get("habr_queries"))


async def fetch(cfg: dict) -> list[Vacancy]:
    out = []
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20) as http:
        for q in cfg["habr_queries"]:
            for page in (1, 2):
                try:
                    r = await http.get(URL, params={"q": q, "type": "all", "sort": "date", "page": page})
                    r.raise_for_status()
                except httpx.HTTPError:
                    log.exception("query %r page %s failed", q, page)
                    break
                for j in r.json()["list"]:
                    # the list endpoint has no description; skills + division + location is the signal
                    extra = [s["title"] for s in j.get("skills") or []]
                    extra += [d["title"] for d in j.get("divisions") or []]
                    extra += [loc["title"] for loc in j.get("locations") or []]
                    if j.get("remoteWork"):
                        extra.append("remote")
                    if j.get("salaryQualification"):
                        extra.append(j["salaryQualification"]["title"])
                    out.append(
                        Vacancy(
                            source=NAME,
                            ext_id=str(j["id"]),
                            title=j["title"],
                            company=(j.get("company") or {}).get("title", ""),
                            url="https://career.habr.com" + j["href"],
                            salary=(j.get("salary") or {}).get("formatted", ""),
                            text=", ".join(extra),
                        )
                    )
                await asyncio.sleep(1)  # be polite
    return out
