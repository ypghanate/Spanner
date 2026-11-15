"""Live shopping on voice request: replacement parts for the RK68, or other keyboards. No mocks.

    POST /coach/shop  {"query": "show me replacement keycaps"}   (the user's spoken words)

- A replaceable part (keycap, switch, stabilizer, cable...): Linkup (structured search) finds real offers
  with price and whether they fit the RK68, and why.
- Another keyboard ("show me other keyboards", "what else could I buy"): Linkup finds real alternatives
  with price, pros and cons compared with the RK68.
- Tavily finds a real product photo for every item, in parallel.
Results are cached per request for 30 min (same question in the demo = instant answer).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from functools import lru_cache

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/coach", tags=["shop"])
MODEL = "Royal Kludge RK68"
_pool = ThreadPoolExecutor(max_workers=8)
_search_pool = ThreadPoolExecutor(max_workers=4)
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "shop_cache"
CACHE_TTL_S = 6 * 3600          # web prices move slowly; the demo must survive restarts
ENRICH_BUDGET_S = 4.0           # wait this long for photos and missing prices, then answer and finish in the background
_inflight: set[str] = set()


def _cache_file(key: str) -> Path:
    return CACHE_DIR / (re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:80] + ".json")


def _cache_get(key: str) -> dict | None:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    f = _cache_file(key)
    if f.exists() and time.time() - f.stat().st_mtime < CACHE_TTL_S:
        try:
            out = json.loads(f.read_text(encoding="utf-8"))
            _cache[key] = (f.stat().st_mtime, out)
            return out
        except Exception:
            return None
    return None


def _cache_put(key: str, out: dict) -> None:
    _cache[key] = (time.time(), out)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_file(key).write_text(json.dumps(out), encoding="utf-8")
    except Exception as e:
        print(f"shop cache write failed: {e}")


def prefetch(text: str) -> None:
    """Search ahead of the question (a repair was just planned): by the time they ask, the answer is cached."""
    kind, item = classify(text)
    key = f"{kind}|{item}"
    if _cache_get(key) or key in _inflight:
        return

    def run():
        try:
            shop(text)
        except Exception as e:
            print(f"shop prefetch '{text}' failed: {e}")
    _pool.submit(run)
_cache: dict[str, tuple[float, dict]] = {}

PARTS = [  # (spoken words, part to search for)
    (r"key ?caps?|caps?\b", "keycaps (MX-compatible, 65% / 68-key set or single keycaps)"),
    (r"switch", "mechanical switches (MX-compatible, hot-swap, 3-pin)"),
    (r"stabili[sz]er|space ?bar", "stabilizers (2u / 6.25u, plate-mounted, 65% keyboard)"),
    (r"cable|usb", "USB-C cable for a mechanical keyboard"),
    (r"puller|tool", "keycap and switch puller tool"),
    (r"battery", "replacement battery"),
    (r"case|shell", "65% keyboard case"),
]


class PartOffer(BaseModel):
    name: str = Field(description="Product name as listed")
    retailer: str
    price: float = Field(description="Price as a number, 0 if not shown")
    currency: str = Field(description="ISO code, e.g. GBP, USD, EUR")
    url: str = Field(description="Product page URL")
    image_url: str = Field(description="Direct URL of the product image if present on the page, else empty")
    compatible: bool = Field(description=f"True if it fits the {MODEL} (hot-swap, MX-style, 65% layout)")
    compatibility: str = Field(description="One short sentence: why it fits or doesn't fit the RK68")


class PartSearch(BaseModel):
    offers: list[PartOffer]


class GenericOffer(BaseModel):
    name: str = Field(description="Product name as listed")
    retailer: str
    price: float = Field(description="Price as a number, 0 if not shown")
    currency: str = Field(description="ISO code, e.g. GBP, USD, EUR")
    url: str = Field(description="Product page URL")
    image_url: str = Field(description="Direct URL of the product image if present on the page, else empty")
    compatible: bool = Field(description="True if it fits the exact model asked about")
    compatibility: str = Field(description="One short sentence: why it fits or doesn't fit that model")


class GenericSearch(BaseModel):
    offers: list[GenericOffer]


class GenericAlternative(BaseModel):
    name: str = Field(description="Product name")
    price: float = Field(description="Typical price as a number, 0 if unknown")
    currency: str
    url: str = Field(description="The manufacturer's official product page URL")
    image_url: str = Field(description="Direct URL of a product image if present, else empty")
    pros: list[str] = Field(description="2-3 short pros compared with the model asked about")
    cons: list[str] = Field(description="1-3 short cons compared with the model asked about")


class GenericAlternatives(BaseModel):
    products: list[GenericAlternative]


_ALTERNATIVES = re.compile(r"\b(other|another|alternative|different|new|better|competitor)s?\b|instead of|upgrade|should i buy|what else", re.I)


def _search_generic_alternatives(model: str) -> tuple[list[dict], list[dict]]:
    """Competitors of an object learned live: what to buy instead, with pros and cons, from reviews."""
    res, sources = _structured_deep(
        (f"Best alternatives to the {model} to buy in 2026, from reviews on sites like RTINGS, The Verge, Wirecutter, "
         f"TechRadar or What Hi-Fi. Give 4-6 real competing products with the official product page URL, a typical price with "
         f"currency, a product image URL, and 2-3 pros and 1-3 cons compared with the {model}."), GenericAlternatives.model_json_schema())
    items = [GenericAlternative.model_validate(o).model_dump() for o in (res or {}).get("products", []) if str(o.get("url", "")).startswith("http")]
    return items, sources


def _search_generic(part: str, model: str) -> tuple[list[dict], list[dict]]:
    """Replacement parts or accessories for an object Spanner learned live: Linkup, sourced, no keyboard assumptions."""
    res, sources = _structured(
        (f"Buy {part} for the {model}. Find 4-6 real product pages whose price is shown on the page (the manufacturer's "
         f"store, Amazon UK, eBay UK, iFixit, specialist shops). For each: name, retailer, the listed price and currency, URL, "
         f"product image URL, and whether it fits the {model} exactly and why."), GenericSearch.model_json_schema())
    offers = [GenericOffer.model_validate(o).model_dump() for o in (res or {}).get("offers", []) if str(o.get("url", "")).startswith("http")]
    return offers, sources


class KeyboardOption(BaseModel):
    name: str = Field(description="Keyboard model name")
    price: float = Field(description="Typical price as a number, 0 if unknown")
    currency: str
    url: str = Field(description="The manufacturer's official product page URL")
    image_url: str = Field(description="Direct URL of a product image if present, else empty")
    pros: list[str] = Field(description="2-3 short pros of the keyboard itself compared with the RK68")
    cons: list[str] = Field(description="1-3 short cons of the keyboard itself compared with the RK68")


class KeyboardSearch(BaseModel):
    keyboards: list[KeyboardOption]


class ShopRequest(BaseModel):
    query: str
    model: str = ""       # the object learned live ("Apple AirPods Pro 2"); empty = the keyboard


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


_META = re.compile(r"\b(no verified|not (provide|specif|list|includ|mention)|unknown|unavailable|available "
                   r"information|information (does|is)|price (was )?not|not (be )?verified|source|does not|"
                   r"doesn.t|no direct|image url|not identified|according to|identifies|lists it|the verge)\b", re.I)
_MAJOR = {"GBP", "USD", "EUR"}


def _clean_keyboard(k: dict, generic: bool = False) -> dict:
    """Drop pros/cons that talk about missing data instead of the product, and prices that can't be right."""
    k["pros"] = [x for x in k.get("pros", []) if not _META.search(x)][:3]
    k["cons"] = [x for x in k.get("cons", []) if not _META.search(x)][:3]
    cur = (k.get("currency") or "").upper()
    lo, hi = (1, 5000) if generic else (25, 600)                             # a real 65% board is £25-£600; other objects vary
    if cur not in _MAJOR or not lo <= float(k.get("price") or 0) <= hi:
        k["price"], k["currency"] = 0.0, ""                                  # re-read from the store page below
    return k


def _to_gbp(i: dict) -> None:
    """A GBP figure next to every price, at today's ECB rate (the user is in the UK)."""
    from backend.parts_agent import fx_rate

    cur = (i.get("currency") or "").upper()
    if i.get("price") and cur and cur != "GBP":
        r = fx_rate(cur, "GBP")
        if r:
            i["price_gbp"] = round(float(i["price"]) * r, 2)
    elif i.get("price") and cur == "GBP":
        i["price_gbp"] = float(i["price"])


def _enrich(item: dict) -> dict:
    if not item.get("image_url", "").startswith("http"):
        item["image_url"], item["image_source"] = _image_for(item["name"]), "tavily"
    if not item.get("price"):
        price, cur = _price_for(item["name"], item["url"])
        if price:
            item["price"], item["currency"], item["price_source"] = price, cur or item.get("currency", ""), "web"
    return item


def _structured(query: str, schema) -> tuple[dict, list[dict]]:
    """Linkup structured search; standard first, then once more, then deep (results vary run to run).
    Returns (data, sources) - the sources are the pages Linkup read, shown as citations."""
    def one(depth: str):
        try:
            res = _linkup().search(query=query, depth=depth, output_type="structured",
                                   structured_output_schema=schema, include_sources=True)
        except Exception as e:
            print(f"linkup {depth} search failed: {e}")
            return {}, [], False
        data = _data(res)
        srcs = [{"title": getattr(x, "name", "") or "", "url": getattr(x, "url", "")}
                for x in (getattr(res, "sources", None) or []) if getattr(x, "url", "")]
        items = next(iter(data.values()), []) if isinstance(data, dict) else []
        return data, srcs, any(str(i.get("url", "")).startswith("http") for i in items)

    # two standard searches side by side (results vary run to run): the first good one wins; deep only if both fail
    last: tuple = ({}, [], False)
    for fut in as_completed([_search_pool.submit(one, "standard") for _ in range(2)]):
        last = fut.result()
        if last[2]:
            return last[0], last[1]
    data, srcs, _ok = one("deep")
    return (data, srcs) if data else (last[0], last[1])


def _search_parts(part: str) -> tuple[list[dict], list[dict]]:
    res, sources = _structured(
        (f"Buy {part} that fit the {MODEL} 65% hot-swappable mechanical keyboard. Find 4-6 real product "
               "pages whose price is shown on the page: the Royal Kludge store, AliExpress, eBay UK, KBDfans, "
               "Keychron, Akko, Epomaker, Mechanical Keyboards UK, at most 2 from Amazon. For each: name, retailer, "
               "the listed price and currency, URL, product image URL, and whether it fits the RK68 and why."),
        PartSearch)
    offers = PartSearch.model_validate(res or {"offers": []}).offers
    return [o.model_dump() for o in offers if o.url.startswith("http")][:6], sources


def _structured_deep(query: str, schema) -> tuple[dict, list[dict]]:
    """Deep first (reads review + store pages: better models, prices, pros and cons), then standard."""
    for depth in ("deep", "standard"):
        try:
            res = _linkup().search(query=query, depth=depth, output_type="structured",
                                   structured_output_schema=schema, include_sources=True)
        except Exception as e:
            print(f"linkup {depth} search failed: {e}")
            continue
        data = _data(res)
        items = next(iter(data.values()), []) if isinstance(data, dict) else []
        if sum(str(i.get("url", "")).startswith("http") for i in items) >= 3:
            return data, [{"title": getattr(x, "name", "") or "", "url": getattr(x, "url", "")}
                          for x in (getattr(res, "sources", None) or []) if getattr(x, "url", "")]
    return {}, []


def _search_keyboards() -> tuple[list[dict], list[dict]]:
    res, sources = _structured_deep(
        (f"Best alternatives to the {MODEL} 65% mechanical keyboard to buy in 2026, from reviews on sites like "
               "RTINGS, The Verge, Tom's Hardware or PC Gamer: e.g. Keychron, Akko, Epomaker, Wooting, Royal Kludge. "
               "Find 4-5 real current models. For each: the price on the manufacturer's official store page (USD, "
               "GBP or EUR), that official product page URL, a product image URL, and 2-3 pros and 1-3 cons compared "
               "with the RK68 about the keyboard itself (switches, build, connectivity, layout, software, price)."),
        KeyboardSearch)
    kbs = KeyboardSearch.model_validate(res or {"keyboards": []}).keyboards
    return [k.model_dump() for k in kbs if k.url.startswith("http")][:5], sources


def shop(text: str, model: str = "") -> dict:
    if model and model.lower() != MODEL.lower():          # an object learned live: parts or competitors for that model
        if _ALTERNATIVES.search(text):
            kind, item = "keyboard", f"alternatives to the {model}"       # "keyboard" = the viewer's pros/cons renderer
        else:
            kind, item = "part", re.sub(r"^(show me|find|buy|get|i need|where can i (buy|get))\s+", "", text.strip(), flags=re.I) or text
        key = f"generic|{model}|{kind}|{item}"
    else:
        model = ""
        kind, item = classify(text)
        key = f"{kind}|{item}"
    hit = _cache_get(key)
    if hit and not hit.get("partial"):
        if kind == "part" and not model:
            verified_status(item)
        return {**hit, "cached": True}
    _inflight.add(key)
    try:
        return _shop_live(kind, item, key, model)
    finally:
        _inflight.discard(key)


def _shop_live(kind: str, item: str, key: str, model: str = "") -> dict:
    t0 = time.perf_counter()
    if kind == "part" and not model:
        verified_status(item)          # start the deep verification agent now; it runs while we search
    if model:
        found, sources = _search_generic_alternatives(model) if kind == "keyboard" else _search_generic(item, model)
    else:
        found, sources = _search_keyboards() if kind == "keyboard" else _search_parts(item)
    names: set[str] = set()
    items = []
    for i in found:   # Linkup sometimes lists the same product twice
        k = re.sub(r"[^a-z0-9]", "", i["name"].lower())[:30]
        if k not in names:
            names.add(k)
            items.append(i)
    if not items:
        raise HTTPException(502, "Linkup found nothing for that, try asking again")
    if kind == "keyboard":
        items = [_clean_keyboard(i, generic=bool(model)) for i in items]
    # photo + price for each item, in parallel; answer after ENRICH_BUDGET_S and finish the rest in the background
    futs = [_pool.submit(_enrich, i) for i in items]
    done, pending = wait(futs, timeout=ENRICH_BUDGET_S)
    out = _finish(kind, item, items, sources, t0, partial=bool(pending), model=model)
    _cache_put(key, out)
    if pending:
        def complete():
            wait(pending)
            _cache_put(key, _finish(kind, item, items, sources, t0, partial=False, model=model))
        _pool.submit(complete)
    return out


def _finish(kind: str, item: str, items: list[dict], sources: list[dict], t0: float, partial: bool, model: str = "") -> dict:
    items = [dict(i) for i in items]
    for i in items:
        _to_gbp(i)
    items.sort(key=lambda i: (not i.get("compatible", True), i.get("price", 0) <= 0, i.get("price", 0)))
    out = {"kind": kind, "item": item, "items": items, "model": model or MODEL, "generic": bool(model),
           "latency_ms": round((time.perf_counter() - t0) * 1000), "engines": ["linkup", "tavily"],
           "sources": sources[:12], "sources_count": len(sources), "partial": partial}
    if kind == "keyboard":             # "acts": before you buy a new one, what would fixing yours cost?
        card = _disk_card(PARTS[1][0])
        if card and card.get("decision"):
            out["repair_instead"] = {"headline": card["decision"]["headline"], "as_of": card["as_of"]}
    return out


# ---------------------------------------------------------------- verified layer (backend.parts_agent)
FAULT = "one key no longer registers or double-types; replace the failed part"
CARD_MAX_AGE_H = 12


def _rx_for(item: str) -> str:
    return next((rx for rx, part in PARTS if part == item), r"switch")


def _disk_card(rx: str) -> dict | None:
    """The newest live research card for this part saved by the parts agent today (data/parts/*.json)."""
    from datetime import datetime, timezone
    from pathlib import Path

    from backend.parts_agent import ReplacementCard, card_view

    best = None
    for f in (Path(__file__).resolve().parents[1] / "data" / "parts").glob("*.json"):
        try:
            c = ReplacementCard.model_validate(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
        if c.mock or "rk61" not in c.identity.name().lower() or not re.search(rx, c.part_needed, re.I):
            continue
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(c.searched_at)).total_seconds() / 3600
        if age_h <= CARD_MAX_AGE_H and (best is None or c.searched_at > best.searched_at):
            best = c
    return card_view(best) if best else None


_jobs: dict[str, str] = {}


def verified_status(item: str) -> dict:
    """{"status": "ready"|"researching"|"failed", "card": {...}} for this part.
    Starts the deep Linkup agent (offers -> verify fit on 2+ independent sites -> recall check -> new-unit
    price -> repair-or-replace) once; while it runs, today's last card for the same part is shown."""
    from backend.parts_agent import get_card, start_research

    if item not in _jobs:
        _jobs[item] = start_research(item.split(" (")[0], FAULT, model_text=MODEL)["job"]
    live = get_card(_jobs[item])
    if live.get("status") == "ready":
        return {**live, "fresh": True}
    prev = _disk_card(_rx_for(item))
    if prev:
        return {"status": "ready", "card": prev, "fresh": False, "refreshing": live.get("status") == "researching"}
    if live.get("status") == "failed":
        _jobs.pop(item, None)      # let the next ask retry
    return live


@router.get("/shop/verified")
async def verified_route(query: str):
    kind, item = classify(query)
    if kind != "part":
        raise HTTPException(400, "verification is for replacement parts")
    return await asyncio.to_thread(verified_status, item)


@router.post("/shop")
async def shop_route(req: ShopRequest):
    if not req.query.strip():
        raise HTTPException(400, "empty query")
    return await asyncio.to_thread(shop, req.query, req.model.strip())


WARM = ["show me replacement switches", "show me replacement keycaps", "show me replacement stabilizers",
        "show me a replacement USB-C cable", "show me other keyboards"]  # parts ones also start verification


def warm() -> None:
    """Run the demo's likely questions live once at startup, so the first ask on stage is instant."""
    for q in WARM:
        kind, item = classify(q)
        if _cache_get(f"{kind}|{item}"):
            continue                     # already on disk from an earlier run: no need to pay for it again
        try:
            shop(q)
        except Exception as e:
            print(f"shop warm-up '{q}' failed: {e}")
