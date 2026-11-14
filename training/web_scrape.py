"""Tavily: find and extract web repair guides for the configured repair.

Each page is saved to data/web/<page_id>.json with its URL and title (provenance), then
Claude reads it and, if it really is a step-by-step guide, extracts a Procedure into
data/procedures/web/<page_id>.json. Pages that aren't guides are recorded and skipped.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

MAX_PAGE_CHARS = 60_000   # very long pages are cut here (logged); guides are far shorter
MIN_PAGE_CHARS = 400      # anything shorter is not a usable guide

# YouTube is links-only (VISION.md): tutorials reach users as links via the Linkup card, never as data
EXCLUDED_DOMAINS = ["youtube.com", "youtu.be"]

QUERY_TEMPLATES = [
    "{object} {fault} how to fix step by step",
    "{object} {fault} repair guide",
    "{object} {fault} ifixit",
]


def page_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def queries(cfg: dict) -> list[str]:
    custom = cfg.get("web_queries") or []
    return custom or [q.format(object=cfg["object"], fault=cfg["fault"]) for q in QUERY_TEMPLATES]


def scrape(cfg: dict, out_dir: Path, max_results: int = 5, mock: bool = False) -> list[dict]:
    """Search + extract. Returns the saved pages (cached pages are reused)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if mock:
        pages = [{"url": f"https://example.com/mock-guide-{i}", "title": f"Mock guide {i}",
                  "query": "mock", "raw_content": "Step 1: do the mock thing. " * 40} for i in range(2)]
    else:
        from tavily import TavilyClient

        client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
        found: dict[str, dict] = {}
        for q in queries(cfg):
            res = client.search(q, search_depth="advanced", max_results=max_results,
                                include_raw_content=True,
                                exclude_domains=EXCLUDED_DOMAINS)
            for r in res.get("results", []):
                found.setdefault(r["url"], {"url": r["url"], "title": r.get("title", ""), "query": q,
                                            "raw_content": r.get("raw_content") or ""})
        # Pages where search returned no full text: fetch them with extract (max 20 URLs per call)
        missing = [u for u, p in found.items() if len(p["raw_content"]) < MIN_PAGE_CHARS]
        for i in range(0, len(missing), 20):
            ext = client.extract(urls=missing[i:i + 20], extract_depth="advanced")
            for r in ext.get("results", []):
                if r.get("url") in found:
                    found[r["url"]]["raw_content"] = r.get("raw_content") or ""
        pages = list(found.values())

    saved = []
    for p in pages:
        if any(d in p["url"] for d in EXCLUDED_DOMAINS):
            continue
        text = p["raw_content"] or ""
        if len(text) < MIN_PAGE_CHARS:
            print(f"   skip (no usable text): {p['url']}")
            continue
        if len(text) > MAX_PAGE_CHARS:
            print(f"   note: cut {p['url']} from {len(text)} to {MAX_PAGE_CHARS} characters")
            text = text[:MAX_PAGE_CHARS]
        pid = page_id(p["url"])
        record = {"page_id": pid, "url": p["url"], "title": p["title"], "query": p["query"],
                  "raw_content": text, "fetched_at": datetime.now(timezone.utc).isoformat()}
        path = out_dir / f"{pid}.json"
        if not path.exists():
            path.write_text(json.dumps(record, indent=1), encoding="utf-8")
        saved.append(json.loads(path.read_text(encoding="utf-8")))
    return saved
