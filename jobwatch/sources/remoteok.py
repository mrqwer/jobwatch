import httpx

from ..config import UA
from ..db import Vacancy
from ..scoring import strip_html

NAME = "remoteok"
URL = "https://remoteok.com/api"  # ToS: link back to the vacancy url (we do, it is the url we send)


def enabled(cfg: dict) -> bool:
    return cfg.get("remoteok", True)


async def fetch(cfg: dict) -> list[Vacancy]:
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=30) as http:
        r = await http.get(URL)
        r.raise_for_status()
    out = []
    for j in r.json()[1:]:  # [0] is the legal notice
        lo, hi = j.get("salary_min"), j.get("salary_max")
        out.append(
            Vacancy(
                source=NAME,
                ext_id=str(j["id"]),
                title=j.get("position", ""),
                company=j.get("company", ""),
                url=j["url"],
                salary=f"${lo}-{hi}" if lo and hi else "",
                text=strip_html(j.get("description", "")) + " " + ", ".join(j.get("tags") or []),
            )
        )
    return out
