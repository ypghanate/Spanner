"""Linkup research agent: the part you need, verified for your exact model, with sources.

When Spanner has diagnosed a part that must be replaced, this agent researches the live web
with Linkup and returns a ReplacementCard for the app to show next to the 3D animation:

  1. identify  Claude reads the model label photo -> brand, model, model number, year
  2. offers    Linkup deep search (structured)    -> where to buy the part, price, stock, URL
  3. verify    Linkup sourced answer per offer     -> does it fit THIS model? Claude judges the
               evidence; "verified" needs 2+ independent sites, otherwise it says "unverified"
  4. manual    Linkup finds the official manual (PDF or page), Claude extracts the specs,
               steps and warnings relevant to this repair, with page numbers
  5. tutorials Linkup finds YouTube tutorials (links only; videos are never downloaded)

Every claim on the card carries the URL it came from. Results are cached in data/parts/ and
data/manuals/ and synced to Databricks (bronze_part_cards, bronze_part_offers, bronze_manual_facts).

  python -m backend.parts_agent --model "Beta RR 300 2019" --part "rear brake pads" --fault "worn to the backing plate"
  python -m backend.parts_agent --label-image label.jpg --part "..." --fault "..."   [--mock] [--force] [--view]

For the live agent: `TOOL` + `handle_tool()` let Claude (Pass 2) start research itself; it returns at
once and `get_card(job)` delivers the card when ready. `card_view()` is what the app renders.
Stage safety: warm the cache with --force before the demo, then set PARTS_OFFLINE=1 if Wi-Fi is bad.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PARTS_DIR = ROOT / "data" / "parts"
MANUALS_DIR = ROOT / "data" / "manuals"
MANUAL_PROCEDURES_DIR = ROOT / "data" / "procedures" / "manuals"
CLAUDE_MODEL = os.getenv("PARTS_AGENT_MODEL", "claude-opus-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_PDF_BYTES = 30_000_000
MAX_OFFERS_TO_VERIFY = 4
CACHE_HOURS = float(os.getenv("PARTS_CACHE_HOURS", "24"))  # prices go stale; re-research after this


# ---------------------------------------------------------------- data shapes
class ModelIdentity(BaseModel):
    brand: str
    model: str
    model_number: str = Field(description="Exact model/part number printed on the label, empty if not visible.")
    year_or_revision: str = Field(description="Year, revision or variant if visible, else empty.")
    category: str = Field(description="What kind of object, e.g. 'enduro motorbike', 'laptop'.")
    evidence: str = Field(description="The text actually read from the label.")
    confidence: float = Field(description="0 to 1.")

    def name(self) -> str:
        return " ".join(x for x in [self.brand, self.model, self.model_number, self.year_or_revision] if x)


class Offer(BaseModel):
    part_name: str
    part_number: str = Field(description="Manufacturer or retailer part number, empty if not shown.")
    retailer: str
    price: float = Field(description="Price as a number, 0 if not shown.")
    currency: str = Field(description="ISO code like GBP, EUR, USD.")
    in_stock: Literal["yes", "no", "unknown"]
    url: str = Field(description="The product page URL.")
    compatible_models_listed: list[str] = Field(description="Models the product page says it fits.")


class OfferSearch(BaseModel):
    offers: list[Offer]


class CompatibilityVerdict(BaseModel):
    fits: Literal["yes", "no", "unclear"]
    reason: str = Field(description="One sentence citing what the sources say.")
    supporting_urls: list[str] = Field(description="Only URLs from the provided sources that support the verdict.")


class ManualSpec(BaseModel):
    name: str
    value: str
    unit: str
    page: str = Field(description="Page number in the manual, or empty.")


class ManualFacts(BaseModel):
    is_official_manual: bool = Field(description="True if this is the manufacturer's own manual for this model.")
    language: str = Field(description="Language the document is written in, e.g. 'English', 'Chinese'.")
    has_repair_procedure: bool = Field(description="True only if the manual actually describes how to do this "
                                                   "repair (not just that a part exists or to contact a dealer).")
    title: str
    specs: list[ManualSpec] = Field(description="Specs relevant to this repair: sizes, torques, part numbers, fluids.")
    steps: list[str] = Field(description="The manual's steps for this repair, in order, with page numbers.")
    warnings: list[str]


class OfferCheck(BaseModel):
    offer: Offer
    verdict: CompatibilityVerdict
    verified: bool
    independent_sources: int


class NewProduct(BaseModel):
    product_name: str
    price: float = Field(description="Price of a brand-new unit, 0 if not shown.")
    currency: str
    retailer: str
    url: str
    weight_kg: float = Field(description="Product weight in kg from its spec sheet, 0 if not stated.")


class NewProductSearch(BaseModel):
    products: list[NewProduct]


class RepairDecision(BaseModel):
    """The agent's decision for the user: is repairing worth it? Computed, not guessed."""
    verdict: Literal["repair", "replace", "unclear"]
    repair_cost: float
    new_price: float
    currency: str
    savings: float
    ewaste_kg_avoided: float
    reason: str
    sources: list[str]


class RecallCheck(BaseModel):
    status: Literal["recall_found", "none_found", "unclear"]
    summary: str = Field(description="One sentence; name the recall and date if there is one.")
    urls: list[str] = Field(description="Only URLs from the provided sources.")


class ReplacementCard(BaseModel):
    card_id: str
    identity: ModelIdentity
    part_needed: str
    fault: str
    options: list[OfferCheck]
    decision: RepairDecision | None = None
    recall: RecallCheck | None = None
    manual_url: str = ""
    manual: ManualFacts | None = None
    tutorials: list[dict] = []
    sources: list[str] = []
    searched_at: str
    mock: bool = False


# ---------------------------------------------------------------- clients
class _RetryingLinkup:
    """LinkupClient with retries on dropped connections (venue Wi-Fi drops long deep searches)."""

    def __init__(self, client, attempts: int = 3):
        self._c, self._attempts = client, attempts

    def _call(self, fn, *args, **kwargs):
        import time

        import httpx

        for i in range(self._attempts):
            try:
                return fn(*args, **kwargs)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                if i == self._attempts - 1:
                    raise
                print(f"   Linkup connection dropped ({type(e).__name__}); retrying...")
                time.sleep(2 * (i + 1))

    def search(self, *args, **kwargs):
        return self._call(self._c.search, *args, **kwargs)

    def fetch(self, *args, **kwargs):
        return self._call(self._c.fetch, *args, **kwargs)


def _linkup():
    from linkup import LinkupClient

    return _RetryingLinkup(LinkupClient(api_key=os.environ["LINKUP_API_KEY"]))


def _claude(system: str, content, schema):
    import anthropic

    resp = anthropic.Anthropic().beta.messages.parse(
        model=CLAUDE_MODEL, max_tokens=16000, system=system,
        messages=[{"role": "user", "content": content}], output_format=schema,
        output_config={"effort": "medium"}, betas=[FALLBACK_BETA], fallbacks="default")
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"Claude returned no result (stop_reason={resp.stop_reason})")
    return resp.parsed_output


PARTS_COUNTRY = os.getenv("PARTS_COUNTRY", "United Kingdom")
PARTS_CURRENCY = os.getenv("PARTS_CURRENCY", "GBP")


def _market() -> str:
    """Where the user shops, so offers and new-unit prices are comparable (same currency)."""
    return (f"Prefer shops that sell to customers in the {PARTS_COUNTRY}, with prices in {PARTS_CURRENCY}; "
            "include others only if none are found.")


def _q(identity: ModelIdentity) -> str:
    """The model quoted and disambiguated, as Linkup's query rules recommend: '"Sony WH-1000XM4" headphones'."""
    return f'"{identity.name()}"' + (f" {identity.category}" if identity.category else "")


def _fetch_markdown(url: str) -> str:
    """Linkup Fetch for a known URL: JavaScript rendering on by default, 'pro' mode if the page comes back empty."""
    client = _linkup()
    page = client.fetch(url=url, render_js=True)
    text = page.markdown or ""
    if len(text) < 200:
        text = client.fetch(url=url, render_js=True, mode="pro").markdown or text
    return text


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------- steps
def identify(label_image: Path | None, model_text: str | None, mock: bool) -> ModelIdentity:
    if mock or (label_image is None and model_text):
        text = model_text or "Mock Brand Model 100"
        parts = text.split()
        return ModelIdentity(brand=parts[0], model=" ".join(parts[1:]) or text, model_number="",
                             year_or_revision="", category="", evidence=f"typed: {text}",
                             confidence=1.0 if model_text else 0.5)
    data = base64.standard_b64encode(label_image.read_bytes()).decode()
    media = "image/png" if label_image.suffix.lower() == ".png" else "image/jpeg"
    return _claude(
        "You read product labels, rating plates and VIN/serial stickers. Report only text you can "
        "actually read; leave fields empty rather than guess.",
        [{"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
         {"type": "text", "text": "Identify the exact product model from this label."}],
        ModelIdentity)


def find_offers(identity: ModelIdentity, part: str, mock: bool) -> tuple[list[Offer], list[str]]:
    if mock:
        offers = [Offer(part_name=f"{part} (mock {i})", part_number=f"MOCK-{i}", retailer=f"shop{i}.example",
                        price=10.0 + i * 3, currency="GBP", in_stock="yes",
                        url=f"https://shop{i}.example/p/{i}", compatible_models_listed=[identity.name()])
                  for i in range(3)]
        return offers, [o.url for o in offers]
    # Deep search results vary run to run, so try three query shapes before giving up:
    # 1. full retrieval plan (deep)  2. plain shopping query (deep)  3. plain query (standard)
    queries = [
        (f"Find online shops currently selling a replacement {part} for the {_q(identity)}. {_market()} "
         "Run separate searches for: the manufacturer's official spare-parts store, major retailers, and "
         "specialist spare-parts shops. Scrape each product page found and extract: part name, part number, "
         "retailer, price, currency, stock status, product URL, and the models the page says it fits. "
         "Return the source URL for each offer; return an empty list if none are found.", "deep"),
        (f"Buy {part} compatible with the {_q(identity)}: find product pages (manufacturer store, Amazon, "
         "specialist keyboard/parts shops, AliExpress) and extract part name, part number, retailer, price, "
         "currency, stock status, product URL and listed compatible models. Include offers in any currency.", "deep"),
        (f"{_q(identity)} replacement {part} buy price", "standard"),
    ]
    sources: list[str] = []
    seen: set[str] = set()
    offers: list[Offer] = []
    for query, depth in queries:  # union of all three shapes: deep search results vary run to run
        try:
            res = _linkup().search(query=query, depth=depth, output_type="structured",
                                   structured_output_schema=OfferSearch, include_sources=True)
        except Exception as e:
            print(f"   offers ({depth}) failed: {e}")
            continue
        data = res.data if hasattr(res, "data") else res
        found = OfferSearch.model_validate(data if isinstance(data, dict) else json.loads(data))
        sources += [s.url for s in (getattr(res, "sources", None) or [])]
        for o in found.offers:
            if o.url and o.url.startswith("http") and o.url not in seen:
                seen.add(o.url)
                offers.append(o)
    offers.sort(key=lambda o: (o.price <= 0, o.price))
    return offers, sources


def verify(identity: ModelIdentity, offer: Offer, mock: bool) -> OfferCheck:
    if mock:
        v = CompatibilityVerdict(fits="yes", reason="mock", supporting_urls=[offer.url, "https://forum.example/t/1"])
        return OfferCheck(offer=offer, verdict=v, verified=True, independent_sources=2)
    ref = offer.part_number or offer.part_name
    # standard: parallel facet searches + scraping the one URL we already know (the offer page)
    ans = _linkup().search(
        query=(f'Check whether the part "{ref}" ({offer.part_name}) fits the {_q(identity)}. Scrape {offer.url} '
               "and report which models it lists. Run separate searches for: this part number together with the "
               "model name, the manufacturer's compatibility or fitment list, and retailer or forum fitment notes. "
               "Give the source URL for each piece of evidence; say none found if no source names both the part "
               "and this model."),
        depth="standard", output_type="sourcedAnswer")
    source_urls = [s.url for s in ans.sources]
    verdict = _claude(
        "You check whether a replacement part fits a specific model. Judge only from the evidence given. "
        "'yes' needs explicit evidence: either a source names this exact model (or its model number), or the part "
        "is a standard interface (e.g. an MX-style 3-pin/5-pin mechanical switch, a USB-C cable) and a source says the "
        "model uses that interface (e.g. 'hot-swappable, 3-pin/5-pin switches'). Otherwise 'unclear'.",
        f"Model: {identity.name()}\nPart: {ref} ({offer.part_name}) from {offer.url}\n"
        f"Product page lists compatible models: {offer.compatible_models_listed}\n\n"
        f"Web answer: {ans.answer}\n\nSources:\n" + "\n".join(f"- {s.url}: {s.snippet}" for s in ans.sources),
        CompatibilityVerdict)
    allowed = set(source_urls) | {offer.url}
    verdict.supporting_urls = [u for u in verdict.supporting_urls if u in allowed]  # no invented links
    domains = {_domain(u) for u in verdict.supporting_urls}
    model_keys = [k.lower() for k in (identity.model_number, identity.model) if k]
    if any(k in m.lower() for m in offer.compatible_models_listed for k in model_keys):
        domains.add(_domain(offer.url))  # the retailer's own fitment list counts as one source
    verified = verdict.fits == "yes" and len(domains) >= 2
    return OfferCheck(offer=offer, verdict=verdict, verified=verified, independent_sources=len(domains))


def find_manual(identity: ModelIdentity, part: str, fault: str, mock: bool) -> tuple[str, ManualFacts | None]:
    if mock:
        return "https://manufacturer.example/manual.pdf", ManualFacts(
            is_official_manual=True, language="English", has_repair_procedure=True, title="Mock manual",
            specs=[ManualSpec(name="Caliper bolt torque", value="25", unit="Nm", page="42")],
            steps=["Remove the pad pin (p. 42)", "Slide out the pads (p. 42)"], warnings=["Mock warning"])
    # search to discover the manual's URL, then fetch it (Linkup: fetch when the URL is known)
    res = _linkup().search(
        query=(f"Find the official English-language owner's manual, help guide or service manual for the "
               f"{_q(identity)}, preferably the PDF published by {identity.brand} or its official support site "
               f"for the {PARTS_COUNTRY}. Return the document URLs and titles; say none found if there is none."),
        depth="standard", output_type="searchResults", max_results=8)
    results = [r for r in res.results if getattr(r, "url", None)]
    brand = identity.brand.lower().replace(" ", "")
    results.sort(key=lambda r: (brand not in _domain(r.url).replace("-", ""), not r.url.lower().endswith(".pdf")))
    question = (f"Repair: replace the {part} on a {identity.name()} (fault: {fault}). Extract the specs, steps "
                "and warnings from this manual that matter for this repair, with page numbers.")
    system = ("You extract technical facts from product manuals. Quote values exactly as written; never "
              "estimate. If the document isn't a manual for this model, set is_official_manual to false. "
              "Leave steps empty unless the manual really describes this repair; never write a step saying "
              "that there is no procedure.")
    fallback: tuple[str, ManualFacts] | None = None
    for r in results[:6]:  # some official sites block fetching; try a few copies
        try:
            text = _fetch_markdown(r.url)  # pages and PDFs, as Markdown
            if len(text) >= 500:
                content = f"{question}\n\nDOCUMENT ({r.url}):\n{text}"
            elif r.url.lower().endswith(".pdf"):  # Fetch couldn't read it: give Claude the PDF itself
                import httpx

                pdf = httpx.get(r.url, follow_redirects=True, timeout=60).content
                if not pdf.startswith(b"%PDF") or len(pdf) > MAX_PDF_BYTES:
                    continue
                MANUALS_DIR.mkdir(parents=True, exist_ok=True)
                (MANUALS_DIR / f"{hashlib.sha1(r.url.encode()).hexdigest()[:12]}.pdf").write_bytes(pdf)
                content = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                           "data": base64.standard_b64encode(pdf).decode()}},
                           {"type": "text", "text": question}]
            else:
                continue
            facts = _claude(system, content, ManualFacts)
            if not facts.is_official_manual:
                continue
            if facts.language.lower().startswith("english"):
                return r.url, facts
            fallback = fallback or (r.url, facts)  # keep a non-English manual only as a last resort
        except Exception as e:
            print(f"   manual candidate failed: {r.url} ({e})")
    return fallback or ("", None)


REPAIR_THRESHOLD = float(os.getenv("REPAIR_THRESHOLD", "0.6"))  # repair if parts cost <= 60% of a new unit


def find_new_product(identity: ModelIdentity, mock: bool) -> tuple[NewProduct | None, list[str]]:
    """What a brand-new unit costs today (and its weight, for the e-waste figure)."""
    if mock:
        return NewProduct(product_name=identity.name(), price=249.0, currency="GBP", retailer="newshop.example",
                          url="https://newshop.example/item", weight_kg=0.25), ["https://newshop.example/item"]
    # deep: prices live on product pages that must be found, then scraped (standard returned price 0)
    res = _linkup().search(
        query=(f"Find the current price of a brand-new {_q(identity)}. {_market()} Run separate searches for: "
               f"the manufacturer's official store, major retailers, and the official {identity.brand} spec sheet "
               "(for the product weight). Scrape each product page and the spec page, then return product name, "
               "retailer, price, currency, product URL and weight in kg, with the source URL for each; use 0 for "
               "anything not found."),
        depth="deep", output_type="structured", structured_output_schema=NewProductSearch, include_sources=True)
    data = res.data if hasattr(res, "data") else res
    found = NewProductSearch.model_validate(data if isinstance(data, dict) else json.loads(data))
    priced = sorted((p for p in found.products if p.price > 0 and p.url),
                    key=lambda p: (p.currency != PARTS_CURRENCY, p.price))  # home currency first
    best = priced[0] if priced else None
    weights = [p.weight_kg for p in found.products if p.weight_kg > 0]   # weight may come from the spec page row
    if best and not best.weight_kg and weights:
        best = best.model_copy(update={"weight_kg": weights[0]})
    return best, [s.url for s in (getattr(res, "sources", None) or [])]


_FX_CACHE: dict[tuple[str, str], float] = {}


def fx_rate(src: str, dst: str) -> float | None:
    """Today's exchange rate (European Central Bank data via frankfurter.dev, no key). None if unavailable."""
    if src == dst:
        return 1.0
    if (src, dst) not in _FX_CACHE:
        try:
            import httpx

            r = httpx.get("https://api.frankfurter.dev/v1/latest", params={"base": src, "symbols": dst},
                          timeout=5, follow_redirects=True)
            _FX_CACHE[(src, dst)] = float(r.json()["rates"][dst])
        except Exception:
            return None
    return _FX_CACHE[(src, dst)]


def decide(checks: list[OfferCheck], new: NewProduct | None, fx=None) -> RepairDecision | None:
    """Repair or replace, from the cheapest verified offer (or cheapest offer, flagged) vs a new unit.
    `fx(src, dst) -> rate` converts part prices into the new unit's currency when they differ."""
    priced = [c for c in checks if c.offer.price > 0 and c.verdict.fits != "no"]
    if not priced or new is None:
        return None
    # Bring every offer into the new unit's currency (today's rate) so a verified part priced in another
    # currency still beats an unverified local one; offers that can't be converted are dropped.
    comparable = []
    for c in priced:
        if c.offer.currency == new.currency:
            comparable.append(c)
        elif fx is not None and (rate := fx(c.offer.currency, new.currency)):
            o = c.offer.model_copy(update={"price": round(c.offer.price * rate, 2), "currency": new.currency,
                                           "part_name": f"{c.offer.part_name} (converted from "
                                                        f"{c.offer.price:.2f} {c.offer.currency})"})
            comparable.append(c.model_copy(update={"offer": o}))
    priced = comparable or priced
    best = min((c for c in priced if c.verified), default=None, key=lambda c: c.offer.price) \
        or min(priced, key=lambda c: c.offer.price)
    if best.offer.currency != new.currency:
        return RepairDecision(verdict="unclear", repair_cost=best.offer.price, new_price=new.price,
                              currency=best.offer.currency, savings=0, ewaste_kg_avoided=0,
                              reason=f"Prices are in different currencies ({best.offer.currency} vs {new.currency}).",
                              sources=[best.offer.url, new.url])
    repair = best.offer.price <= REPAIR_THRESHOLD * new.price
    note = "" if best.verified else " (the cheapest part isn't verified for this model yet)"
    return RepairDecision(
        verdict="repair" if repair else "replace", repair_cost=best.offer.price, new_price=new.price,
        currency=new.currency, savings=round(new.price - best.offer.price, 2),
        ewaste_kg_avoided=new.weight_kg if repair else 0,
        reason=(f"The part costs {best.offer.price:.2f} vs {new.price:.2f} for a new one"
                f" ({best.offer.price / new.price:.0%} of the price){note}."),
        sources=[best.offer.url, new.url])


def check_recall(identity: ModelIdentity, mock: bool) -> RecallCheck | None:
    """Fresh information only: safety recalls or notices for this model in the last 2 years."""
    if mock:
        return RecallCheck(status="none_found", summary="No recalls found in the last 2 years (mock).", urls=[])
    from datetime import date, timedelta

    since = date.today() - timedelta(days=730)
    ans = _linkup().search(  # a timeframe is genuinely implied here, so a date filter is appropriate
        query=(f"Find official safety recalls or safety notices for the {_q(identity)} issued since "
               f"{since:%B %Y}. Run separate searches for: the manufacturer's recall or safety notices page, "
               "and government product-safety recall databases. Return each recall's title, date and source "
               "URL; say none found if there are none."),
        depth="standard", output_type="sourcedAnswer", from_date=since)
    rc = _claude(
        "You check product safety recalls. Report a recall only if a source explicitly names this model. "
        "If nothing relevant is in the sources, say none_found; if it's ambiguous, unclear.",
        f"Model: {identity.name()}\nWeb answer: {ans.answer}\n\nSources:\n"
        + "\n".join(f"- {s.url}: {s.snippet}" for s in ans.sources), RecallCheck)
    allowed = {s.url for s in ans.sources}
    rc.urls = [u for u in rc.urls if u in allowed]
    return rc


_CHAPTER = re.compile(r"(?m)^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–—:]?\s*(.{3,80})$")


def _seconds(ts: str) -> int:
    n = [int(x) for x in ts.split(":")]
    return n[0] * 60 + n[1] if len(n) == 2 else n[0] * 3600 + n[1] * 60 + n[2]


def best_chapter(description: str, part: str, fault: str) -> tuple[int, str] | None:
    """Pick the video chapter (from 'MM:SS Title' lines in the description) that best matches the repair."""
    words = {w for w in re.findall(r"[a-z]{3,}", f"{part} {fault}".lower())}
    best, best_score = None, 0
    for ts, title in _CHAPTER.findall(description or ""):
        score = len(words & set(re.findall(r"[a-z]{3,}", title.lower())))
        if score > best_score:
            best, best_score = (_seconds(ts), title.strip()), score
    return best


def find_tutorials(identity: ModelIdentity, part: str, mock: bool, fault: str = "") -> list[dict]:
    """YouTube tutorials as links only (never downloaded). If the video description lists chapters,
    the link jumps straight to the chapter about this part (&t=<seconds>)."""
    if mock:
        return [{"title": "Mock tutorial", "url": "https://www.youtube.com/watch?v=mock&t=95",
                 "chapter": "Replacing the pads", "start_s": 95}]
    res = _linkup().search(
        query=(f"Find video tutorials showing step by step how to replace the {part} on the {_q(identity)}. "
               "Return the video titles and URLs."),
        depth="standard", output_type="searchResults", include_domains=["youtube.com"], max_results=5)
    videos = [{"title": r.name, "url": r.url, "chapter": "", "start_s": 0}
              for r in res.results if "youtube.com/watch" in r.url][:3]
    for v in videos[:2]:  # read the description for chapters; skip quietly if the page can't be fetched
        try:
            hit = best_chapter(_fetch_markdown(v["url"]), part, fault)
            if hit:
                v["start_s"], v["chapter"] = hit
                v["url"] = f"{v['url']}{'&' if '?' in v['url'] else '?'}t={hit[0]}"
        except Exception:
            pass
    return videos


# ---------------------------------------------------------------- orchestration
def _safe(fn, *args):
    """Optional extras (new price, recall check) must never sink the whole card."""
    try:
        return fn(*args)
    except Exception as e:
        print(f"   optional step {getattr(fn, '__name__', fn)} failed: {e}")
        return None


def _card_id(identity: ModelIdentity, part: str) -> str:
    return hashlib.sha1(f"{identity.name()}|{part}".lower().encode()).hexdigest()[:12]


def _cached(card_id: str, max_age_hours: float | None) -> ReplacementCard | None:
    path = PARTS_DIR / f"{card_id}.json"
    if not path.exists():
        return None
    card = ReplacementCard.model_validate_json(path.read_text(encoding="utf-8"))
    if max_age_hours is None:
        return card
    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(card.searched_at)).total_seconds() / 3600
    return card if age_h <= max_age_hours else None


def research(part: str, fault: str, label_image: Path | None = None, model_text: str | None = None,
             mock: bool = False, force: bool = False, repair_id: str = "",
             max_age_hours: float = CACHE_HOURS) -> ReplacementCard:
    """Full research. Cached cards younger than max_age_hours are returned as they are.
    With PARTS_OFFLINE=1 (the stage setting when Wi-Fi is bad) only cached cards are used."""
    offline = os.getenv("PARTS_OFFLINE") == "1"
    mock = mock or (not offline and not os.getenv("LINKUP_API_KEY"))
    identity = identify(label_image, model_text, mock or offline)
    card_id = _card_id(identity, part)
    out = PARTS_DIR / f"{card_id}.json"
    if not force:
        hit = _cached(card_id, None if offline else max_age_hours)
        if hit:
            return hit
    if offline:
        raise LookupError(f"PARTS_OFFLINE=1 and no cached card for {identity.name()} / {part}. "
                          "Warm the cache first: python -m backend.parts_agent ... --force")

    with ThreadPoolExecutor(max_workers=4) as pool:  # everything else runs while offers are searched
        manual_f = pool.submit(find_manual, identity, part, fault, mock)
        tut_f = pool.submit(find_tutorials, identity, part, mock, fault)
        new_f = pool.submit(find_new_product, identity, mock)
        recall_f = pool.submit(_safe, check_recall, identity, mock)
        offers, offer_sources = find_offers(identity, part, mock)
        checks = list(pool.map(lambda o: verify(identity, o, mock), offers[:MAX_OFFERS_TO_VERIFY]))
        manual_url, manual = manual_f.result()
        tutorials = tut_f.result()
        new_product, new_sources = _safe(new_f.result) or (None, [])
        recall = recall_f.result()

    checks.sort(key=lambda c: (not c.verified, c.verdict.fits != "yes", c.offer.price <= 0, c.offer.price))
    decision = decide(checks, new_product, fx=fx_rate)
    sources = sorted({*offer_sources, *new_sources, *(u for c in checks for u in c.verdict.supporting_urls),
                      *([manual_url] if manual_url else []), *(t["url"] for t in tutorials),
                      *(decision.sources if decision else []), *(recall.urls if recall else [])})
    card = ReplacementCard(card_id=card_id, identity=identity, part_needed=part, fault=fault, options=checks[:3],
                           decision=decision, recall=recall, manual_url=manual_url, manual=manual,
                           tutorials=tutorials, sources=sources,
                           searched_at=datetime.now(timezone.utc).isoformat(), mock=mock)
    PARTS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(card.model_dump_json(indent=1), encoding="utf-8")
    if manual and manual.has_repair_procedure and manual.steps:
        _manual_to_knowledge_base(card, repair_id)
    return card


def _manual_to_knowledge_base(card: ReplacementCard, repair_id: str) -> None:
    """The manual's steps become a procedure in the knowledge base (source_kind 'manual')."""
    from training.schemas import Procedure, ProcedureRecord, ProcedureStep

    m = card.manual
    specs = "; ".join(f"{s.name}: {s.value} {s.unit}" + (f" (p. {s.page})" if s.page else "") for s in m.specs)
    steps = [ProcedureStep(index=i + 1, title=s[:80], instruction=s, parts=[card.part_needed], tools=[],
                           expected_visual_state="", common_mistakes=[], safety_notes=m.warnings if i == 0 else [],
                           start_s=0, end_s=0) for i, s in enumerate(m.steps)]
    rec = ProcedureRecord(procedure_id=f"manual_{card.card_id}", repair_id=repair_id or card.card_id,
                          source_kind="manual", source_videos=[card.card_id],
                          sources=[{"url": card.manual_url, "title": m.title, "kind": "manual"}],
                          procedure=Procedure(object=card.identity.name(), fault=card.fault, tools_needed=[],
                                              parts=[card.part_needed], steps=steps, notes=f"Specs: {specs}"))
    MANUAL_PROCEDURES_DIR.mkdir(parents=True, exist_ok=True)
    (MANUAL_PROCEDURES_DIR / f"{card.card_id}.json").write_text(rec.model_dump_json(indent=1), encoding="utf-8")


# ---------------------------------------------------------------- what the app shows
_SYMBOL = {"GBP": "£", "EUR": "€", "USD": "$"}


def card_view(card: ReplacementCard) -> dict:
    """The card exactly as the app should render it: badges, prices, 'as of' time, a link on every claim."""
    model = card.identity.name()
    when = datetime.fromisoformat(card.searched_at).strftime("%d %b %H:%M")
    options = []
    for c in card.options:
        o = c.offer
        price = f"{_SYMBOL.get(o.currency, o.currency + ' ')}{o.price:.2f}" if o.price > 0 else "price on site"
        badge = (f"✓ Fits your {model} · {c.independent_sources} sources" if c.verified
                 else f"? Unverified for {model}" if c.verdict.fits != "no" else f"✗ Doesn't fit {model}")
        options.append({"retailer": o.retailer, "part": o.part_name, "part_number": o.part_number,
                        "price": price, "in_stock": o.in_stock, "url": o.url, "verified": c.verified,
                        "badge": badge, "why": c.verdict.reason, "evidence": c.verdict.supporting_urls})
    manual = None
    if card.manual:
        key = card.manual.specs[0] if card.manual.specs else None
        manual = {"title": card.manual.title, "url": card.manual_url,
                  "key_fact": (f"{key.name}: {key.value} {key.unit}".strip() + (f" (p. {key.page})" if key.page else ""))
                  if key else "", "steps": card.manual.steps[:5], "warnings": card.manual.warnings}
    decision = None
    if card.decision:
        d, sym = card.decision, _SYMBOL.get(card.decision.currency, card.decision.currency + " ")
        headline = {"repair": f"Repair it: {sym}{d.repair_cost:.2f} in parts vs {sym}{d.new_price:.2f} new. "
                              f"Saves {sym}{d.savings:.2f}"
                              + (f" and {d.ewaste_kg_avoided:g} kg of e-waste" if d.ewaste_kg_avoided else ""),
                    "replace": f"Replacing may be better: parts {sym}{d.repair_cost:.2f} vs {sym}{d.new_price:.2f} new",
                    "unclear": "Repair vs replace: not enough price data"}[d.verdict]
        decision = {"verdict": d.verdict, "headline": headline, "why": d.reason, "sources": d.sources}
    safety = None
    if card.recall and card.recall.status != "none_found":
        safety = {"status": card.recall.status, "text": card.recall.summary, "sources": card.recall.urls}
    return {"card_id": card.card_id, "title": f"Replace the {card.part_needed}", "model": model,
            "model_read_from": card.identity.evidence, "decision": decision, "safety": safety,
            "recall_checked": card.recall is not None, "options": options, "manual": manual,
            "tutorial": card.tutorials[0] if card.tutorials else None,
            "as_of": f"Prices as of {when} UTC", "sources_count": len(card.sources), "mock": card.mock}


# ---------------------------------------------------------------- for the live agent (non-blocking)
_pool = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, Future] = {}


def start_research(part: str, fault: str, model_text: str | None = None,
                   label_image: Path | None = None) -> dict:
    """Kick off research in the background and return at once. Deep searches take tens of seconds,
    so the live loop never waits: poll get_card() and push the card to the app when it's ready."""
    key = f"{model_text or label_image}|{part}".lower()
    if key not in _jobs or _jobs[key].done() and _jobs[key].exception():
        _jobs[key] = _pool.submit(research, part, fault, label_image, model_text)
    return {"status": "researching", "job": key}


def get_card(job: str) -> dict:
    f = _jobs.get(job)
    if f is None:
        return {"status": "unknown_job"}
    if not f.done():
        return {"status": "researching"}
    if f.exception():
        return {"status": "failed", "error": str(f.exception())}
    return {"status": "ready", "card": card_view(f.result())}


# Tool definition for the brain (Claude Pass 2) so the agent itself decides when to look for a part.
TOOL = {
    "name": "find_replacement_part",
    "description": ("Start a live web search for a replacement part when the diagnosed fault needs a part to be "
                    "bought (worn, snapped, cracked, missing). Finds offers, verifies compatibility with the exact "
                    "model on 2+ independent sources, the official manual's specs and a tutorial; every claim is "
                    "cited. Returns immediately with a job id; the card is pushed to the app when ready. Don't "
                    "call it for repairs that need no new part."),
    "input_schema": {"type": "object", "additionalProperties": False,
                     "properties": {"part": {"type": "string", "description": "The part to replace, e.g. 'rear brake pads'."},
                                    "fault": {"type": "string", "description": "What is wrong with it."},
                                    "model": {"type": "string", "description": "Exact model from the label, e.g. 'Beta RR 300 2019'."}},
                     "required": ["part", "fault", "model"]},
}


def handle_tool(tool_input: dict) -> dict:
    return start_research(tool_input["part"], tool_input["fault"], model_text=tool_input["model"])


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description="Linkup research agent: replacement part, manual, tutorials")
    ap.add_argument("--part", required=True, help="the part that must be replaced")
    ap.add_argument("--fault", default="", help="what's wrong with it")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--label-image", type=Path, help="photo of the model label / rating plate")
    g.add_argument("--model", help="model as text, e.g. 'Beta RR 300 2019'")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore the cache (use this to warm it before the demo)")
    ap.add_argument("--view", action="store_true", help="also print the card as the app renders it")
    a = ap.parse_args()

    from training import tracking

    with tracking.run("parts-research", {"part": a.part, "model": a.model or str(a.label_image),
                                         "mock": a.mock}) as mlf:
        card = research(a.part, a.fault, a.label_image, a.model, mock=a.mock, force=a.force)
        mlf.log_metrics({"options": len(card.options), "verified_options": sum(c.verified for c in card.options),
                         "sources": len(card.sources), "manual_found": int(card.manual is not None),
                         "tutorials": len(card.tutorials)})
        mlf.log_artifact(str(PARTS_DIR / f"{card.card_id}.json"), "cards")

    print(f"\n{card.identity.name()}: replace the {card.part_needed}" + ("  [MOCK]" if card.mock else ""))
    view = card_view(card)
    if view["decision"]:
        print(f"  DECISION: {view['decision']['headline']}")
    if card.recall:
        print(f"  SAFETY: {card.recall.status} - {card.recall.summary}")
    for c in card.options:
        badge = "VERIFIED" if c.verified else f"fits: {c.verdict.fits}"
        print(f"  {c.offer.price:>8.2f} {c.offer.currency}  {c.offer.retailer:<20} {badge:<12} {c.offer.url}")
        print(f"           {c.verdict.reason}  ({c.independent_sources} independent sources)")
    if card.manual:
        print(f"  manual: {card.manual.title} <- {card.manual_url}")
        for s in card.manual.specs[:5]:
            print(f"     {s.name}: {s.value} {s.unit}" + (f" (p. {s.page})" if s.page else ""))
    for t in card.tutorials:
        print(f"  tutorial: {t['title']}" + (f" [jumps to {t['chapter']}]" if t.get("chapter") else "")
              + f" <- {t['url']}")
    print(f"  saved: data/parts/{card.card_id}.json ({len(card.sources)} sources)")
    if a.view:
        print(json.dumps(card_view(card), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
