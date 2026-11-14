"""Live shopping on voice request: replacement parts for the RK61, or other keyboards. No mocks.

    POST /coach/shop  {"query": "show me replacement keycaps"}   (the user's spoken words)

- A replaceable part (keycap, switch, stabilizer, cable...): Linkup (structured search) finds real offers
  with price and whether they fit the RK61, and why.
- Another keyboard ("show me other keyboards", "what else could I buy"): Linkup finds real alternatives
  with price, pros and cons compared with the RK61.
- Tavily finds a real product photo for every item, in parallel.
Results are cached per request for 30 min (same question in the demo = instant answer).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/coach", tags=["shop"])
MODEL = "Royal Kludge RK61"
_pool = ThreadPoolExecutor(max_workers=8)
_cache: dict[str, tuple[float, dict]] = {}

PARTS = [  # (spoken words, part to search for)
    (r"key ?caps?|caps?\b", "keycaps (MX-compatible, 60% / 61-key set or single keycaps)"),
    (r"switch", "mechanical switches (MX-compatible, hot-swap, 3-pin)"),
    (r"stabili[sz]er|space ?bar", "stabilizers (2u / 6.25u, plate-mounted, 60% keyboard)"),
    (r"cable|usb", "USB-C cable for a mechanical keyboard"),
    (r"puller|tool", "keycap and switch puller tool"),
    (r"battery", "replacement battery"),
    (r"case|shell", "60% keyboard case"),
]


class PartOffer(BaseModel):
    name: str = Field(description="Product name as listed")
    retailer: str
    price: float = Field(description="Price as a number, 0 if not shown")
    currency: str = Field(description="ISO code, e.g. GBP, USD, EUR")
    url: str = Field(description="Product page URL")
    image_url: str = Field(description="Direct URL of the product image if present on the page, else empty")
    compatible: bool = Field(description=f"True if it fits the {MODEL} (hot-swap, MX-style, 60% layout)")
    compatibility: str = Field(description="One short sentence: why it fits or doesn't fit the RK61")


class PartSearch(BaseModel):
    offers: list[PartOffer]


class KeyboardOption(BaseModel):
    name: str = Field(description="Keyboard model name")
    price: float = Field(description="Typical price as a number, 0 if unknown")
    currency: str
    url: str = Field(description="Product or review page URL")
    image_url: str = Field(description="Direct URL of a product image if present, else empty")
    pros: list[str] = Field(description="2-3 short pros compared with the RK61")
    cons: list[str] = Field(description="1-3 short cons compared with the RK61")


class KeyboardSearch(BaseModel):
    keyboards: list[KeyboardOption]


class ShopRequest(BaseModel):
    query: str


def classify(text: str) -> tuple[str, str]:
    t = text.lower()
    if re.search(r"other keyboards?|another keyboard|new keyboard|alternatives?|instead|upgrade|better keyboard|"
                 r"different keyboard|buy a keyboard|keyboards", t) and not re.search(r"key ?caps?", t):
        return "keyboard", "keyboard"
    for rx, part in PARTS:
        if re.search(rx, t):
            return "part", part
    return "part", PARTS[1][1]   # the repair is a switch swap: default to switches


@lru_cache(maxsize=1)
def _linkup():
    from backend.parts_agent import _linkup as make

    return make()


@lru_cache(maxsize=1)
def _tavily():
    from tavily import TavilyClient

    return TavilyClient(api_key=os.environ["TAVILY_API_KEY"])


def _data(res) -> dict:
    data = res.data if hasattr(res, "data") else res
    return data if isinstance(data, dict) else json.loads(data)


def _image_for(name: str) -> str:
    try:
        r = _tavily().search(query=f"{name} product photo", include_images=True, max_results=3,
                             exclude_domains=["youtube.com", "youtu.be"])
        imgs = [i if isinstance(i, str) else i.get("url", "") for i in r.get("images", [])]
        return next((u for u in imgs if u.startswith("http")), "")
    except Exception:
        return ""


class PagePrice(BaseModel):
    price: float = Field(description="The product's current selling price on this page, 0 if not visible")
    currency: str = Field(description="ISO code: GBP, USD, EUR...")


@lru_cache(maxsize=1)
def _claude():
    import anthropic

    return anthropic.Anthropic()


def _price_for(name: str, url: str) -> tuple[float, str]:
    """A real current price. Amazon blocks page fetches, so: Tavily search snippets for this exact product
    (+ the product page via Linkup fetch when reachable), and Claude Haiku picks the price that belongs to it."""
    texts: list[str] = []

    def snippets() -> None:
        r = _tavily().search(query=f'"{name}" price', max_results=6, search_depth="advanced",
                             exclude_domains=["youtube.com", "youtu.be", "reddit.com"])
        texts.extend(f"[{x.get('url')}] {x.get('title', '')}: {x.get('content', '')}" for x in r.get("results", []))

    def page() -> None:
        texts.append(f"[{url}] " + ((_linkup().fetch(url=url, render_js=True).markdown or "")[:8000]))

    for job in (snippets, page):
        try:
            job()
        except Exception:
            pass
    text = "\n\n".join(t for t in texts if len(t) > 40)
    if not text:
        return 0.0, ""
    try:
        resp = _claude().beta.messages.parse(
            model="claude-haiku-5-5", max_tokens=200, thinking={"type": "disabled"},
            messages=[{"role": "user", "content": (
                f"Product: {name}\nProduct page: {url}\n\nWeb results:\n{text[:14000]}\n\n"
                "What does this exact product currently sell for? Use a price only if it clearly belongs to this "
                "product (same item and pack size), preferring the product page's retailer. Else price 0.")}],
            output_format=PagePrice)
        out = resp.parsed_output
        return (out.price, out.currency) if out and out.price > 0 else (0.0, "")
    except Exception:
        return 0.0, ""


def _enrich(item: dict) -> dict:
    if not item.get("image_url", "").startswith("http"):
        item["image_url"], item["image_source"] = _image_for(item["name"]), "tavily"
    if not item.get("price"):
        price, cur = _price_for(item["name"], item["url"])
        if price:
            item["price"], item["currency"], item["price_source"] = price, cur or item.get("currency", ""), "web"
    return item


def _structured(query: str, schema) -> dict:
    """Linkup structured search; standard first, then once more, then deep (results vary run to run)."""
    last: dict = {}
    for depth in ("standard", "standard", "deep"):
        try:
            last = _data(_linkup().search(query=query, depth=depth, output_type="structured",
                                          structured_output_schema=schema, include_sources=True))
        except Exception as e:
            print(f"linkup {depth} search failed: {e}")
            continue
        items = next(iter(last.values()), []) if isinstance(last, dict) else []
        if any(str(i.get("url", "")).startswith("http") for i in items):
            return last
    return last


def _search_parts(part: str) -> list[dict]:
    res = _structured(
        (f"Buy {part} that fit the {MODEL} 60% hot-swappable mechanical keyboard. Find 4-6 real product "
               "pages whose price is shown on the page: the Royal Kludge store, AliExpress, eBay UK, KBDfans, "
               "Keychron, Akko, Epomaker, Mechanical Keyboards UK, at most 2 from Amazon. For each: name, retailer, "
               "the listed price and currency, URL, product image URL, and whether it fits the RK61 and why."),
        PartSearch)
    offers = PartSearch.model_validate(res or {"offers": []}).offers
    return [o.model_dump() for o in offers if o.url.startswith("http")][:6]


def _search_keyboards() -> list[dict]:
    res = _structured(
        (f"Best alternatives to the {MODEL} 60% mechanical keyboard to buy in 2026 (e.g. Keychron, "
               "Royal Kludge, Akko, Epomaker, Wooting). Find 4-5 real models with typical price, a product page "
               "URL, a product image URL, and 2-3 pros and 1-3 cons compared with the RK61. Pros and cons are about the keyboard itself (switches, build, connectivity, layout, software, price); never list missing information as a con."),
        KeyboardSearch)
    kbs = KeyboardSearch.model_validate(res or {"keyboards": []}).keyboards
    return [k.model_dump() for k in kbs if k.url.startswith("http")][:5]


def shop(text: str) -> dict:
    kind, item = classify(text)
    key = f"{kind}|{item}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 1800:
        return {**hit[1], "cached": True}
    t0 = time.perf_counter()
    found = _search_keyboards() if kind == "keyboard" else _search_parts(item)
    names: set[str] = set()
    items = []
    for i in found:   # Linkup sometimes lists the same product twice
        k = re.sub(r"[^a-z0-9]", "", i["name"].lower())[:30]
        if k not in names:
            names.add(k)
            items.append(i)
    if not items:
        raise HTTPException(502, "Linkup found nothing for that, try asking again")
    items = list(_pool.map(_enrich, items))   # photo + price for each item, in parallel
    items.sort(key=lambda i: (not i.get("compatible", True), i.get("price", 0) <= 0, i.get("price", 0)))
    out = {"kind": kind, "item": item, "items": items, "model": MODEL,
           "latency_ms": round((time.perf_counter() - t0) * 1000), "sources": ["linkup", "tavily"]}
    _cache[key] = (time.time(), out)
    return out


@router.post("/shop")
async def shop_route(req: ShopRequest):
    if not req.query.strip():
        raise HTTPException(400, "empty query")
    return await asyncio.to_thread(shop, req.query)


WARM = ["show me replacement switches", "show me replacement keycaps", "show me other keyboards"]


def warm() -> None:
    """Run the demo's likely questions live once at startup, so the first ask on stage is instant."""
    for q in WARM:
        try:
            shop(q)
        except Exception as e:
            print(f"shop warm-up '{q}' failed: {e}")
