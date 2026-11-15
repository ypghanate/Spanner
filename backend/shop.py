"""Live shopping on request: "order a new battery", "how much is the screen", "show me other headphones". No mocks.

    POST /coach/shop  {"query": "...the person's words...", "session": "..."}

Nothing is hard-coded: Claude (brain/prompts/shop_intent.md) reads the words together with what is on screen (the device,
the repair plan, its parts, the step) and decides what to buy. A clear request is searched; an unclear one comes back as a
question. Then:
- a replacement part: Linkup (structured search) finds real offers with price and whether they fit this product;
- other products to buy instead: Linkup finds real alternatives with price, pros and cons;
- Tavily finds a real product photo for every item, in parallel.
Results are cached by what was resolved (product + part) for a few hours, and nothing is searched until it is asked for.
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
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/coach", tags=["shop"])
_pool = ThreadPoolExecutor(max_workers=8)
_search_pool = ThreadPoolExecutor(max_workers=4)
_cache: dict[str, tuple[float, dict]] = {}
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


class PartOffer(BaseModel):
    name: str = Field(description="Product name as listed")
    retailer: str
    price: float = Field(description="Price as a number, 0 if not shown")
    currency: str = Field(description="ISO code, e.g. GBP, USD, EUR")
    url: str = Field(description="Product page URL")
    image_url: str = Field(description="Direct URL of the product image if present on the page, else empty")
    compatible: bool = Field(description="True if it fits the product the person is repairing")
    compatibility: str = Field(description="One short sentence: why it fits or doesn't fit (say \"your device\", never a model name)")


class PartSearch(BaseModel):
    offers: list[PartOffer]


class KeyboardOption(BaseModel):
    name: str = Field(description="Product model name")
    price: float = Field(description="Typical price as a number, 0 if unknown")
    currency: str
    url: str = Field(description="The manufacturer's official product page URL")
    image_url: str = Field(description="Direct URL of a product image if present, else empty")
    pros: list[str] = Field(description="2-3 short pros of this product compared with the one being repaired")
    cons: list[str] = Field(description="1-3 short cons of this product compared with the one being repaired")


class KeyboardSearch(BaseModel):
    keyboards: list[KeyboardOption]


class ShopRequest(BaseModel):
    query: str
    session: str = ""


class ShopTarget(BaseModel):
    action: Literal["part", "alternatives", "clarify"]
    product: str = Field(default="", description="The thing being repaired, as precisely as the screen says (never an invented model).")
    part: str = Field(default="", description="For `part`: a specific searchable replacement for THIS product, with anything the person said.")
    question: str = Field(default="", description="For `clarify`: one short spoken question offering the likely options.")


def _screen_context(session: str) -> str:
    """What the person is looking at: the device, the repair plan and its parts, the step on screen."""
    from . import coach

    lines: list[str] = []
    plan = (coach._plans.get(session) or {}).get("plan")
    dev = coach._device_parts.get(session)
    st = coach._view_state.get(session)
    if plan:
        lines.append(f"Repair: {plan.get('object')} - {plan.get('title') or plan.get('fault')} (fault: {plan.get('fault')}).")
        if plan.get("target_key"):
            lines.append(f"The key they are fixing: {plan['target_key']}.")
        for step in plan.get("steps", []):
            on = "  <- ON SCREEN NOW" if st and st.screen == "step" and st.step == step["index"] else ""
            lines.append(f"Step {step['index']}: {step['instruction']} (parts: {', '.join(step.get('parts', [])) or 'none'}){on}")
    else:
        lines.append("No repair plan yet.")
    if dev:
        lines.append("The device's parts: " + "; ".join(f"{x['name']} ({x.get('label', '')})" for x in dev["parts"]))
    return chr(10).join(lines)


def resolve(text: str, session: str = "") -> ShopTarget:
    """Spoken words + screen -> what to search for. Nothing is assumed: an unclear request becomes a question."""
    from .planner import _client

    system = (Path(__file__).parent / "brain" / "prompts" / "shop_intent.md").read_text(encoding="utf-8")
    try:
        resp = _client().beta.messages.parse(
            model=os.getenv("SHOP_INTENT_MODEL", "claude-haiku-5-5"), max_tokens=400, thinking={"type": "disabled"},
            system=[{"type": "text", "text": system}],
            messages=[{"role": "user", "content": f"[on screen]{chr(10)}{_screen_context(session)}{chr(10)}{chr(10)}[they said] {text}"}],
            output_format=ShopTarget)
        t = resp.parsed_output
    except Exception as e:
        print(f"shop intent failed: {e}")
        t = None
    if t is None or (t.action == "part" and not t.part.strip()):
        return ShopTarget(action="clarify", question="Which part would you like to order?")
    if t.action == "clarify" and not t.question.strip():
        t.question = "Which part would you like to order?"
    return t


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


def _clean_keyboard(k: dict) -> dict:
    """Drop pros/cons that talk about missing data instead of the keyboard, and prices that can't be right."""
    k["pros"] = [x for x in k.get("pros", []) if not _META.search(x)][:3]
    k["cons"] = [x for x in k.get("cons", []) if not _META.search(x)][:3]
    cur = (k.get("currency") or "").upper()
    if cur not in _MAJOR or not 1 <= float(k.get("price") or 0) <= 5000:    # a believable price for any product
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


def _search_parts(part: str, product: str) -> tuple[list[dict], list[dict]]:
    res, sources = _structured(
        (f"Buy {part}. It must fit the {product}. Find 4-6 real product pages whose price is shown on the page, from "
         "trustworthy sellers: the manufacturer's own store or parts shop, iFixit, Amazon, eBay UK and specialist "
         "retailers for this kind of product, at most 2 from any one retailer. For each: name, retailer, the listed "
         "price and currency, URL, product image URL, and whether it fits the product and why (call it \"your device\", "
         "not by model)."),
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


def _search_alternatives(product: str) -> tuple[list[dict], list[dict]]:
    res, sources = _structured_deep(
        (f"Best alternatives to the {product} to buy in 2026, from reviews on reputable sites (RTINGS, The Verge, "
         "Tom's Hardware, Wirecutter, PC Gamer or similar). Find 4-5 real current models. For each: the price on the "
         "manufacturer's official store page (USD, GBP or EUR), that official product page URL, a product image URL, "
         "and 2-3 pros and 1-3 cons compared with the product being repaired (build, features, price)."),
        KeyboardSearch)
    kbs = KeyboardSearch.model_validate(res or {"keyboards": []}).keyboards
    return [k.model_dump() for k in kbs if k.url.startswith("http")][:5], sources


def shop(text: str, session: str = "") -> dict:
    target = resolve(text, session)
    if target.action == "clarify":
        return {"kind": "clarify", "say": target.question}
    kind = "alternatives" if target.action == "alternatives" else "part"
    product, part = target.product.strip() or "the device", target.part.strip()
    key = f"{kind}|{product}|{part}"
    hit = _cache_get(key)
    if hit:                       # a partial result (photos / prices still arriving) is served too: it upgrades itself in the cache
        if kind == "part":
            verified_status(product, part)
        return {**hit, "cached": True}
    _inflight.add(key)
    try:
        return _shop_live(kind, product, part, key)
    finally:
        _inflight.discard(key)


def _shop_live(kind: str, product: str, part: str, key: str) -> dict:
    t0 = time.perf_counter()
    if kind == "part":
        verified_status(product, part)          # start the deep verification agent now; it runs while we search
    found, sources = _search_alternatives(product) if kind == "alternatives" else _search_parts(part, product)
    names: set[str] = set()
    items = []
    for i in found:   # Linkup sometimes lists the same product twice
        k = re.sub(r"[^a-z0-9]", "", i["name"].lower())[:30]
        if k not in names:
            names.add(k)
            items.append(i)
    if not items:
        raise HTTPException(502, "Linkup found nothing for that, try asking again")
    if kind == "alternatives":
        items = [_clean_keyboard(i) for i in items]
    # photo + price for each item, in parallel; answer after ENRICH_BUDGET_S and finish the rest in the background
    futs = [_pool.submit(_enrich, i) for i in items]
    done, pending = wait(futs, timeout=ENRICH_BUDGET_S)
    out = _finish(kind, product, part, items, sources, t0, partial=bool(pending))
    _cache_put(key, out)
    if pending:
        def complete():
            wait(pending)
            _cache_put(key, _finish(kind, product, part, items, sources, t0, partial=False))
        _pool.submit(complete)
    return out


def _finish(kind: str, product: str, part: str, items: list[dict], sources: list[dict], t0: float, partial: bool) -> dict:
    items = [dict(i) for i in items]
    for i in items:
        _to_gbp(i)
    items.sort(key=lambda i: (not i.get("compatible", True), i.get("price", 0) <= 0, i.get("price", 0)))
    out = {"kind": kind, "product": product, "part": part, "item": part, "items": items,
           "latency_ms": round((time.perf_counter() - t0) * 1000), "engines": ["linkup", "tavily"],
           "sources": sources[:12], "sources_count": len(sources), "partial": partial}
    if kind == "alternatives":             # "acts": before you buy a new one, what would fixing yours cost? (if it was researched)
        card = _disk_card(product, "")
        if card and card.get("decision"):
            out["repair_instead"] = {"headline": card["decision"]["headline"], "as_of": card["as_of"]}
    return out


# ---------------------------------------------------------------- verified layer (backend.parts_agent)
CARD_MAX_AGE_H = 12


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in {"the", "for", "and", "replacement", "your", "device"}}


def _disk_card(product: str, part: str) -> dict | None:
    """The newest live research card for this product (and part, if given) saved by the parts agent recently."""
    from datetime import datetime, timezone

    from backend.parts_agent import ReplacementCard, card_view

    pw, qw = _words(product), _words(part)
    best = None
    for f in (Path(__file__).resolve().parents[1] / "data" / "parts").glob("*.json"):
        try:
            c = ReplacementCard.model_validate(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
        if c.mock or not pw or not (pw & _words(c.identity.name())) or (qw and not (qw & _words(c.part_needed))):
            continue
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(c.searched_at)).total_seconds() / 3600
        if age_h <= CARD_MAX_AGE_H and (best is None or c.searched_at > best.searched_at):
            best = c
    return card_view(best) if best else None


_jobs: dict[str, str] = {}


def verified_status(product: str, part: str) -> dict:
    """{"status": "ready"|"researching"|"failed", "card": {...}} for this part of this product.
    Starts the deep Linkup agent (offers -> verify fit on 2+ independent sites -> recall check -> new-unit
    price -> repair-or-replace) once; while it runs, a recent card for the same product and part is shown."""
    from backend.parts_agent import get_card, start_research

    key = f"{product}|{part}"
    if key not in _jobs:
        _jobs[key] = start_research(part, f"replace the {part} on the {product}", model_text=product)["job"]
    live = get_card(_jobs[key])
    if live.get("status") == "ready":
        return {**live, "fresh": True}
    prev = _disk_card(product, part)
    if prev:
        return {"status": "ready", "card": prev, "fresh": False, "refreshing": live.get("status") == "researching"}
    if live.get("status") == "failed":
        _jobs.pop(key, None)      # let the next ask retry
    return live


@router.get("/shop/verified")
async def verified_route(product: str, part: str):
    if not part.strip():
        raise HTTPException(400, "verification is for replacement parts")
    return await asyncio.to_thread(verified_status, product, part)


@router.post("/shop/resolve")
async def resolve_route(req: ShopRequest):
    """What would be searched for these words, in this session? (No search: for checking and for the UI.)"""
    return (await asyncio.to_thread(resolve, req.query, req.session)).model_dump()


@router.post("/shop")
async def shop_route(req: ShopRequest):
    if not req.query.strip():
        raise HTTPException(400, "empty query")
    return await asyncio.to_thread(shop, req.query, req.session)
