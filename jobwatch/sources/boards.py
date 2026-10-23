"""Public job boards with an open JSON API or RSS feed.

One Board per site; `boards:` in config.yaml picks which ones run. EVERY = minimum seconds between
successful fetches, so heavy feeds and boards that ask for polite use are not hit every 15 minutes.
Every board's terms ask for a link back to the vacancy: the url we send is that link.
"""

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from ..config import UA
from ..db import Vacancy
from ..scoring import strip_html

HOUR = 3600


@dataclass
class Board:
    NAME: str
    parse: Callable[[httpx.AsyncClient, dict], Awaitable[list[Vacancy]]]
    EVERY: int = HOUR

    def enabled(self, cfg: dict) -> bool:
        return self.NAME in cfg.get("boards", [])

    async def fetch(self, cfg: dict) -> list[Vacancy]:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=60, follow_redirects=True) as http:
            return await self.parse(http, cfg)


async def _json(http: httpx.AsyncClient, url: str, **params):
    r = await http.get(url, params=params)
    r.raise_for_status()
    return r.json()


def _salary(lo, hi, cur="") -> str:
    return f"{lo or ''}-{hi or ''} {cur or ''}".strip() if lo or hi else ""


# ---------- JSON APIs ----------

async def remotive(http, cfg):
    data = await _json(http, "https://remotive.com/api/remote-jobs", category="software-dev")
    return [
        Vacancy("remotive", str(j["id"]), j["title"], j["url"], j.get("company_name", ""), j.get("salary") or "",
                f"{j.get('candidate_required_location', '')}. {', '.join(j.get('tags') or [])}. {strip_html(j.get('description', ''))}")
        for j in data["jobs"]
    ]


async def arbeitnow(http, cfg):  # EU, mostly Germany: relocation options
    data = await _json(http, "https://www.arbeitnow.com/api/job-board-api")
    return [
        Vacancy("arbeitnow", j["slug"], j["title"], j["url"], j.get("company_name", ""), "",
                f"{j.get('location', '')}{', remote' if j.get('remote') else ''}. {', '.join(j.get('tags') or [])}. "
                f"{strip_html(html.unescape(j.get('description', '')))}")  # description is HTML-escaped twice
        for j in data["data"]
    ]


async def himalayas(http, cfg):
    out = []
    for q in cfg.get("board_queries", []):
        for j in (await _json(http, "https://himalayas.app/jobs/api/search", q=q))["jobs"]:
            out.append(Vacancy(
                "himalayas", j["guid"], j["title"], j.get("applicationLink") or j["guid"], j.get("companyName", ""),
                _salary(j.get("minSalary"), j.get("maxSalary"), j.get("currency")),
                f"{', '.join(j.get('locationRestrictions') or ['worldwide'])}. {strip_html(j.get('description') or j.get('excerpt', ''))}"))
    return out


async def jobicy(http, cfg):
    out = []
    for q in cfg.get("board_queries", []):
        for j in (await _json(http, "https://jobicy.com/api/v2/remote-jobs", count=50, tag=q)).get("jobs", []):
            out.append(Vacancy(
                "jobicy", str(j["id"]), j["jobTitle"], j["url"], j.get("companyName", ""),
                _salary(j.get("salaryMin"), j.get("salaryMax"), j.get("salaryCurrency")),
                f"{j.get('jobGeo', '')}. {j.get('jobLevel', '')}. {strip_html(j.get('jobDescription') or j.get('jobExcerpt', ''))}"))
    return out


async def workingnomads(http, cfg):
    data = await _json(http, "https://www.workingnomads.com/api/exposed_jobs/")
    return [
        Vacancy("workingnomads", j["url"], j["title"], j["url"], j.get("company_name", ""), "",
                f"{j.get('location', '')}. {j.get('tags', '')}. {strip_html(j.get('description', ''))}")
        for j in data
    ]


async def landingjobs(http, cfg):  # Portugal / EU, has a relocation_paid flag
    out = []
    for offset in (0, 50, 100):
        for j in await _json(http, "https://landing.jobs/api/v1/jobs", limit=50, offset=offset):
            extra = [", ".join(j.get("tags") or [])]
            if j.get("remote"):
                extra.append("remote")
            if j.get("relocation_paid"):
                extra.append("relocation paid")
            body = " ".join(strip_html(j.get(k) or "") for k in ("role_description", "main_requirements", "nice_to_have"))
            out.append(Vacancy(
                "landingjobs", str(j["id"]), j["title"], j["url"], "",
                _salary(j.get("gross_salary_low"), j.get("gross_salary_high"), j.get("currency_code")),
                f"{'. '.join(extra)}. {body}"))
    return out


async def getmatch(http, cfg):  # RU IT market, many remote / relocation offers
    data = await _json(http, "https://getmatch.ru/api/offers", limit=100, offset=0)
    out = []
    for j in data["offers"]:
        locs = ", ".join(f"{l.get('city') or l.get('country')} ({l.get('format')})" for l in j.get("location_requirements") or [])
        out.append(Vacancy(
            "getmatch", str(j["id"]), j["position"], "https://getmatch.ru" + j["url"], (j.get("company") or {}).get("name", ""),
            "" if j.get("salary_hidden") else _salary(j.get("salary_display_from"), j.get("salary_display_to"), j.get("salary_currency")),
            f"{locs}. {strip_html(j.get('offer_description') or '')}"))
    return out


async def hn(http, cfg):  # monthly "Ask HN: Who is hiring?": one top-level comment = one company
    stories = await _json(http, "https://hn.algolia.com/api/v1/search_by_date", tags="story,author_whoishiring", hitsPerPage=10)
    story = next(s for s in stories["hits"] if s["title"].startswith("Ask HN: Who is hiring"))
    sid = int(story["objectID"])
    data = await _json(http, "https://hn.algolia.com/api/v1/search_by_date", tags=f"comment,story_{sid}", hitsPerPage=1000)
    out = []
    for c in data["hits"]:
        if c.get("parent_id") != sid or not c.get("comment_text"):
            continue  # replies to job posts, deleted posts
        text = html.unescape(c["comment_text"])
        first = strip_html(re.split(r"<p>", text, maxsplit=1)[0])  # "Company | Role | Location | REMOTE"
        out.append(Vacancy("hn", c["objectID"], first[:120], f"https://news.ycombinator.com/item?id={c['objectID']}",
                           first.split("|")[0].strip()[:60], "", strip_html(text)))
    return out


# ---------- RSS ----------

XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def rss_items(xml: str) -> list[dict[str, str]]:
    """RSS items as {tag: text}. HTML entities like &nbsp; are not valid XML (Djinni has them): decode first."""
    xml = re.sub(r"&([a-zA-Z][a-zA-Z0-9]*);",
                 lambda m: m.group(0) if m.group(1) in XML_ENTITIES else html.unescape(m.group(0)), xml)
    return [{c.tag.split("}")[-1]: (c.text or "") for c in item} for item in ET.fromstring(xml).findall("./channel/item")]


def rss(name: str, url: str, every: int = HOUR) -> Board:
    async def parse(http, cfg):
        r = await http.get(url)
        r.raise_for_status()
        out = []
        for i in rss_items(r.text):
            link = i.get("link") or i.get("guid", "")
            # everything else (description, region, company, salary...) goes to the scored text
            rest = ". ".join(strip_html(v) for k, v in i.items() if k not in ("title", "link", "guid", "pubDate") and v.strip())
            out.append(Vacancy(name, i.get("guid") or link, html.unescape(i.get("title", "")), link,
                               i.get("company", ""), i.get("salary", ""), rest))
        return out

    return Board(name, parse, every)


BOARDS = [
    Board("remotive", remotive, 6 * HOUR),
    Board("arbeitnow", arbeitnow, 6 * HOUR),  # 4 MB page
    Board("himalayas", himalayas),
    Board("jobicy", jobicy),
    Board("workingnomads", workingnomads),
    Board("landingjobs", landingjobs, 6 * HOUR),
    Board("getmatch", getmatch),
    Board("hn", hn, 6 * HOUR),
    rss("wwr", "https://weworkremotely.com/remote-jobs.rss", 2 * HOUR),  # ~1 MB
    rss("jobspresso", "https://jobspresso.co/?feed=job_feed"),
    rss("djinni", "https://djinni.co/jobs/rss/?primary_keyword=Python"),
    rss("nodesk", "https://nodesk.co/remote-jobs/index.xml"),
]
