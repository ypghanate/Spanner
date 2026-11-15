"""Spanner for any object, live: "AirPods Pro 2, the right earbud is quiet" -> photo + specs (Linkup), a 3D twin (fal),
a sourced repair plan (Tavily guides, then YouTube through Gemini), step pictures (fal), diagnostic questions meanwhile.

    POST /coach/object         {session, text}   start (or refine) the job; answers {say, question, ...status}
    POST /coach/object/answer  {session, text}   an answer to a diagnostic question; returns the next one or the plan
    GET  /coach/object?session=                  status: phases, urls, plan readiness

Nothing is mocked: every phase is a live call or an honest "failed: <why>". One job per session; phases run in parallel
threads and never block each other. Files live under data/objects/<slug>/ and are served at /data/objects.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
OBJECTS = ROOT / "data" / "objects"
INTAKE_MODEL = os.getenv("OBJECT_INTAKE_MODEL", "claude-haiku-5-5")
PLAN_MODEL = os.getenv("OBJECT_PLAN_MODEL", "claude-sonnet-5-5")
TWIN_MODEL = os.getenv("FAL_TWIN_MODEL", "tripo3d/tripo/v2.5/image-to-3d")   # bake-off 15 Nov: Tripo clean and true to the product; Trellis lumpy
TWIN_FALLBACK = os.getenv("FAL_TWIN_FALLBACK", "fal-ai/trellis")
EDIT_MODEL = os.getenv("FAL_EDIT_MODEL", "fal-ai/nano-banana/edit")
TWIN_CAP_S = int(os.getenv("FAL_TWIN_CAP_S", "240"))
MAX_STEP_PICTURES = int(os.getenv("OBJECT_STEP_PICTURES", "6"))
FALLBACK_BETA = "server-side-fallback-2026-07-01"

router = APIRouter(prefix="/coach", tags=["objects"])
_pool = ThreadPoolExecutor(max_workers=8)             # reading guides and videos (slow, many tasks)
_media_pool = ThreadPoolExecutor(max_workers=6)       # photo, twin, step pictures: what the person is waiting to see
_fetch_pool = ThreadPoolExecutor(max_workers=16)      # downloading the candidate photos


def _client():
    """One shared HTTP client for all the photo downloads. A new client per request builds a TLS context each time: with 16 in
    parallel that took 10 s (measured), against 0.5 s for the same 16 images through one shared client."""
    global _http
    if _http is None:
        import httpx

        _http = httpx.Client(follow_redirects=True, timeout=8, limits=httpx.Limits(max_connections=32),
                             headers={"User-Agent": "Mozilla/5.0 (Spanner repair coach)"})
    return _http


_http = None
_jobs: dict[str, "Job"] = {}
KEYBOARD_RE = re.compile(r"\bkey ?boards?\b|\brk ?\d+\b|\bkey ?caps?\b|\bspace ?bar\b|\bkeys?\b", re.I)   # same words as training/viewer/route.js


# ---------------------------------------------------------------- Claude output shapes
class Intake(BaseModel):
    object: str = Field(description="The kind of object, plain words: 'wireless earbuds', 'cordless vacuum', 'mechanical keyboard'.")
    model: str = Field(description="The exact product name and model as the person said it, e.g. 'Apple AirPods Pro 2'. Empty if not given.")
    fault: str = Field(description="What is wrong, one short sentence in plain words. Empty if not said yet.")
    is_keyboard: bool = Field(description="True if this is a mechanical keyboard (Spanner has a dedicated flow for those).")
    model_question: str = Field(default="", description="If the exact model is missing or ambiguous: one short spoken question to pin it down. Else empty.")
    fault_question: str = Field(default="", description="If the fault was not said: one short spoken question asking what is wrong. Else empty.")
    questions: list[str] = Field(default_factory=list, description=(
        "3 to 5 short diagnostic questions, in order, that a good technician would ask next and whose answers change the "
        "repair: when it started, what was tried, visible damage, charging/connection, which side/part. Spoken style."))


class SpecLine(BaseModel):
    name: str
    value: str
    unit: str = ""
    source_url: str = ""


class ProductPick(BaseModel):
    product_name: str = Field(description="Official full product name.")
    official_url: str = Field(default="", description="The manufacturer's product page, if found.")
    image_url: str = Field(description="URL of the best product photo: the whole object, plain or white background, no hands, no text overlay, large.")
    image_alternatives: list[str] = Field(default_factory=list, description="2-3 other good product photo URLs, best first.")
    specs: list[SpecLine] = Field(default_factory=list, description="4-6 key specs with the page each came from.")


class ProductSearch(BaseModel):
    products: list[ProductPick]


# ---------------------------------------------------------------- the job
_BRANDS = {"apple", "sony", "samsung", "bose", "dyson", "google", "microsoft", "lg", "jbl", "anker", "logitech", "nintendo",
           "sennheiser", "royal", "kludge", "xiaomi", "huawei", "oneplus", "asus", "dell", "hp", "lenovo", "philips", "bosch"}


def canonical_slug(slug: str) -> str:
    """An existing folder for the same product written with or without its brand: reuse it."""
    key = "-".join(w for w in slug.split("-") if w not in _BRANDS)
    if OBJECTS.exists():
        for d in sorted(OBJECTS.iterdir()):
            if d.is_dir() and not d.name.startswith("_") and "-".join(w for w in d.name.split("-") if w not in _BRANDS) == key:
                return d.name
    return slug


def slug_of(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]
    return s or hashlib.sha1(text.encode()).hexdigest()[:10]


class Job:
    def __init__(self, session: str, text: str):
        self.session, self.first_text = session, text
        self.answers: list[str] = []
        self.said: list[str] = []           # everything Spanner said in this flow, so no line repeats
        self.skip_video = False
        self.intake: Intake | None = None
        self.asked = 0
        self.slug = ""
        self.fault_slug = ""
        self.dir: Path | None = None
        self.phase: dict[str, dict] = {k: {"status": "pending"} for k in ("intake", "photo", "twin", "plan_web", "plan_video", "pictures")}
        self.photo_url = self.twin_url = ""
        self.specs: list[dict] = []
        self.plan: dict | None = None
        self.plan_source = ""
        self.pictures: dict[int, str] = {}
        self.lock = threading.Lock()
        self.t0 = time.time()

    # -- bookkeeping
    def set(self, phase: str, status: str, **kw) -> None:
        with self.lock:
            self.phase[phase] = {"status": status, "t": round(time.time() - self.t0, 1), **kw}

    def record(self, stage: str, t0: float, ok: bool) -> None:
        try:
            from training.feedback import record_stage_timing

            record_stage_timing(self.session, stage, round((time.time() - t0) * 1000), ok)
        except Exception:
            pass

    def cfg(self) -> dict:
        i = self.intake
        fault = (i.fault if i else "") or self.first_text
        if self.answers:
            fault += ". Details: " + " ".join(self.answers)
        return {"repair_id": self.slug, "object": (i.model if i and i.model else self.first_text), "fault": fault,
                "parts": [], "tools": [], "label_model": PLAN_MODEL, "effort": "low", "segment_seconds": 10,
                "web_queries": [f"{i.model if i else self.first_text} {i.fault if i else ''} repair guide step by step",
                                f"how to fix {i.fault if i else self.first_text} {i.model if i else ''}",
                                f"{i.model if i else self.first_text} teardown repair"]}

    def public(self) -> dict:
        i = self.intake
        next_q = self.next_question()
        return {"session": self.session, "slug": self.slug, "object": i.object if i else "", "model": i.model if i else "",
                "fault": i.fault if i else "", "is_keyboard": bool(i and i.is_keyboard), "phases": self.phase,
                "photo_url": self.photo_url, "twin_url": self.twin_url, "specs": self.specs,
                "plan_ready": self.plan is not None, "plan_source": self.plan_source,
                "pictures": {str(k): v for k, v in self.pictures.items()}, "question": next_q,
                "elapsed_s": round(time.time() - self.t0, 1)}

    def next_question(self) -> str:
        if not self.intake:
            return ""
        if self.intake.model_question and not self.intake.model:
            return self.intake.model_question
        if self.intake.fault_question and not self.intake.fault:
            return self.intake.fault_question
        qs = self.intake.questions
        return qs[self.asked] if self.asked < len(qs) else ""

    # -- phases
    def run_intake(self, text: str) -> Intake:
        import anthropic

        t0 = time.time()
        prior = f"\nEarlier they said: {self.first_text!r}. Answers so far: {self.answers}" if self.answers or text != self.first_text else ""
        resp = anthropic.Anthropic(timeout=20, max_retries=1).beta.messages.parse(
            model=INTAKE_MODEL, max_tokens=700, thinking={"type": "disabled"}, output_config={"effort": "low"},
            system=("The person was asked 'What do you want to fix? Tell me the object and the exact model.' Read what they "
                    "said and fill the fields. Be precise about the model; never invent one. Questions must be the ones a "
                    "repair technician really needs, short, spoken, one idea each." + prior),
            messages=[{"role": "user", "content": text}], output_format=Intake)
        out = resp.parsed_output
        if out is None:
            raise RuntimeError("intake gave no answer")
        self.record("object_intake", t0, True)
        return out

    def run_photo(self) -> None:
        """The product photo first (Tavily image search on the person's own words, seconds), the 3D twin starts the moment
        it is on disk; the official page and the specs (Linkup, slower) land alongside and never gate the twin."""
        t0 = time.time(); self.set("photo", "running")
        name = self.intake.model or self.first_text
        try:
            path = self._download_image(self._image_candidates(name), name)
            self.photo_url = f"/data/objects/{self.slug}/{path.name}"
            self.set("photo", "ready", url=self.photo_url, **{k: v for k, v in self.phase["photo"].items() if k in ("picked", "candidates")}); self.record("object_photo", t0, True)
            _media_pool.submit(self._guard, "twin", self.run_twin)
        except Exception as e:
            self.set("photo", "failed", error=str(e)[:200]); self.record("object_photo", t0, False)
        self._run_specs(name)

    def _run_specs(self, name: str) -> None:
        from backend.shop import _structured

        t0 = time.time()
        try:
            data, sources = _structured(
                f"Find the official product photo and key specifications of the {name} ({self.intake.object}). Prefer the "
                f"manufacturer's page or a major retailer. The photo must show the whole product on a plain or white "
                f"background, with no hands and no text. Also give 4-6 key specs (dimensions, weight, battery, connector, "
                f"materials) with the page each came from.", ProductSearch.model_json_schema())
            picks = (data or {}).get("products") or []
            if not picks:
                raise RuntimeError("Linkup found no product page")
            pick = ProductPick.model_validate(picks[0])
            if self.phase["photo"]["status"] == "failed":          # no image from the image search: Linkup's as a fallback
                try:
                    path = self._download_image([pick.image_url] + pick.image_alternatives, name)
                    self.photo_url = f"/data/objects/{self.slug}/{path.name}"
                    self.set("photo", "ready", url=self.photo_url); _media_pool.submit(self._guard, "twin", self.run_twin)
                except Exception as e:
                    self.set("photo", "failed", error=str(e)[:200])
            self.specs = [s.model_dump() for s in pick.specs][:6]
            (self.dir / "specs.json").write_text(json.dumps({"product_name": pick.product_name, "official_product_url": pick.official_url,
                                                             "specs": self.specs, "sources": sources[:8]}, indent=1), encoding="utf-8")
            self.record("object_specs", t0, True)
        except Exception as e:
            print(f"specs {name}: {str(e)[:120]}", flush=True); self.record("object_specs", t0, False)

    def _image_candidates(self, name: str) -> list[str]:
        """Tavily image search with several wordings in parallel (one query often returns only lone parts or lifestyle shots):
        the complete product on a plain background is what makes a good 3D twin."""
        from backend.shop import _tavily

        queries = [f"{name} product photo white background", f"{name} official product image full product",
                   f"{name} complete set what's in the box", f"{name} front view product shot"]

        def one(q):
            try:
                res = _tavily().search(q, include_images=True, max_results=6)
                return [i if isinstance(i, str) else i.get("url", "") for i in res.get("images", [])]
            except Exception as e:
                print(f"tavily images: {str(e)[:100]}", flush=True)
                return []
        from concurrent.futures import wait

        futs = [_fetch_pool.submit(one, q) for q in queries]
        done, _ = wait(futs, timeout=3.5)
        if not done:
            done, _ = wait(futs, timeout=8, return_when="FIRST_COMPLETED")
        seen, out = set(), []
        for f in futs:                                   # original order, finished queries only
            if f in done:
                for u in f.result():
                    if u and u not in seen:
                        seen.add(u); out.append(u)
        return out[:16]

    def _download_image(self, urls: list[str], name: str = "") -> Path:
        """Fetch the candidates in parallel, then let Claude choose the one that will make a good 3D twin: the whole
        product alone, plain background, nothing floating beside it, no text. One image-to-3D run from the wrong photo
        makes nonsense, so the choice is worth ~1.5 s."""
        import base64
        import io

        import httpx
        from PIL import Image

        def fetch(u):
            try:
                r = _client().get(u)
                r.raise_for_status()
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                return (u, img) if img.width >= 300 and img.height >= 300 else None
            except Exception:
                return None
        from concurrent.futures import wait

        futs = [_fetch_pool.submit(fetch, u) for u in urls if str(u).startswith("http")]
        done, _ = wait(futs, timeout=4.5)
        cands = [f.result() for f in futs if f in done and f.result()]
        if len(cands) < 2:                               # too few so far: give the stragglers a little longer
            done, _ = wait(futs, timeout=6)
            cands = [f.result() for f in futs if f in done and f.result()]
        if not cands:
            raise RuntimeError("no usable product photo (none of the candidates loaded)")
        pick = 0
        if len(cands) > 1:
            try:
                import anthropic

                content = []
                for i, (_, img) in enumerate(cands[:10]):
                    t = img.copy(); t.thumbnail((384, 384)); buf = io.BytesIO(); t.save(buf, "JPEG", quality=78)
                    content += [{"type": "text", "text": f"Image {i}:"},
                                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(buf.getvalue()).decode()}}]
                content.append({"type": "text", "text": f"The product is: {name or 'the object'}. Which image is best for turning THIS product into ONE solid 3D "
                                "model? Hard rules, in order: (1) it must show the complete product as it is sold, not a single component on its own "
                                "(for earbuds: the charging case with the earbuds in it, never one lone earbud or only the case lid; for a vacuum: the whole "
                                "vacuum), (2) every part touches the main body, nothing floating in the air beside or above it, (3) plain or white background, "
                                "no hands, no people, no text, no collage, sharp, (4) a three-quarter or front view. If several qualify prefer the simplest "
                                "outline. If NO image qualifies, answer NONE. Otherwise answer with the image number only."})
                # Haiku, thinking off: 0.8 s against 1.8 s, and it answers with the bare number (Sonnet narrated its choice instead)
                r = anthropic.Anthropic(timeout=25, max_retries=1).messages.create(model=INTAKE_MODEL, max_tokens=8, thinking={"type": "disabled"},
                                                                                    messages=[{"role": "user", "content": content}])
                verdict = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip().upper()
                if "NONE" in verdict:
                    raise RuntimeError("no clean single-object photo among the candidates; no twin (the step pictures carry the visuals)")
                digits = "".join(ch for ch in verdict if ch.isdigit())
                if digits and int(digits) < len(cands[:10]):
                    pick = int(digits)
            except RuntimeError:
                raise
            except Exception as e:
                print(f"photo pick: {str(e)[:100]}", flush=True)
        u, img = cands[pick]
        img.thumbnail((1600, 1600))
        out = self.dir / "photo.jpg"; img.save(out, "JPEG", quality=90)
        self.set("photo", "running", picked=u, candidates=len(cands))
        return out

    def run_twin(self) -> None:
        """fal image-to-3D of the product photo. Explicit queue polling with a cap: the queue host dropped for long
        stretches on the venue network tonight, and the demo must not hang on it."""
        import fal_client
        import httpx

        t0 = time.time(); self.set("twin", "running", model=TWIN_MODEL, step="cleaning the photo")
        try:
            url = self._clean_photo_url()
            self.set("twin", "running", model=TWIN_MODEL, step="building the 3D model")
            glb = None
            for model in (TWIN_MODEL, TWIN_FALLBACK):
                try:
                    glb = self._twin_once(model, url, t0)
                    self.set("twin", "running", model=model); break
                except Exception as e:
                    print(f"twin {model}: {str(e)[:120]}", flush=True)
                    if time.time() - t0 > TWIN_CAP_S:
                        raise
            if glb is None:
                raise RuntimeError("no 3D model from either model")
            (self.dir / "model.glb").write_bytes(glb)
            self.twin_url = f"/data/objects/{self.slug}/model.glb"
            self.set("twin", "ready", url=self.twin_url, bytes=len(glb), model=self.phase["twin"].get("model")); self.record("object_twin", t0, True)
        except Exception as e:
            self.set("twin", "failed", error=str(e)[:200]); self.record("object_twin", t0, False)

    def _clean_photo_url(self) -> str:
        """Bake-off 15 Nov (same photos, 8 variants): a web photo with a lone part or floating bits gives a blob in 3D; the same
        photo cleaned by fal's image editor first (the complete product, studio white, three-quarter view, every part connected)
        gives a proper twin for ~12 s more. Multi-view cost 30 s more for no better result and HD/PBR was slower for nothing.
        The edit is told to keep the exact design (a generator drifts: it once gave AirPods 3 the Pro's ear tips). Falls back to
        the raw photo if the edit fails."""
        import fal_client

        import base64

        raw = (self.dir / "photo.jpg").read_bytes()
        clean = self.dir / "clean.png"
        if clean.exists():                                   # the same product again: reuse the cleaned image
            return fal_client.upload(clean.read_bytes(), "image/png")
        t0 = time.time()
        try:
            name = self.intake.model or self.intake.object
            prompt = (f"Show this exact product alone, as a clean studio product photo on a pure white background, three-quarter front view. "
                      f"The product is: {name}, the complete product as it is sold. Keep its exact design, colours, proportions and details: do "
                      "not add, remove or restyle any feature. Every part is connected to the body; nothing floats in the air; no hands, no text, "
                      "no shadows, no other objects.")
            # inline photo: no upload round trip (the editor accepts data URIs; measured 8.5 s end to end)
            uri = "data:image/jpeg;base64," + base64.standard_b64encode(raw).decode()
            r = fal_client.subscribe(EDIT_MODEL, arguments={"prompt": prompt, "image_urls": [uri]})
            img = (r.get("images") or [r.get("image")])[0]["url"]

            def keep() -> None:                              # saved for reuse and for the pitch, off the critical path
                import httpx

                try:
                    clean.write_bytes(httpx.get(img, timeout=60).content)
                except Exception:
                    pass
            _fetch_pool.submit(keep)
            self.record("object_clean", t0, True)
            return img                                       # Tripo reads it straight from fal's CDN: no download, no re-upload
        except Exception as e:
            print(f"photo cleanup failed ({str(e)[:100]}): using the raw photo", flush=True)
            self.record("object_clean", t0, False)
            return fal_client.upload(raw, "image/jpeg")

    @staticmethod
    def _twin_args(model: str, url: str) -> dict:
        if "trellis" in model:
            return {"image_url": url, "ss_sampling_steps": 8, "slat_sampling_steps": 8, "mesh_simplify": 0.95, "texture_size": 512}
        if "hunyuan" in model:
            return {"input_image_url": url, "textured_mesh": True}
        if "tripo" in model:
            return {"image_url": url, "face_limit": 30000, "texture": "standard"}     # ~2 MB, not 14.5 MB: loads at once on any screen
        return {"image_url": url}

    def _twin_once(self, model: str, url: str, t0: float) -> bytes:
        import fal_client
        import httpx

        handle = fal_client.submit(model, arguments=self._twin_args(model, url))
        result = None
        while time.time() - t0 < TWIN_CAP_S:
            try:
                st = handle.status(with_logs=False)
                if type(st).__name__ == "Completed":
                    result = handle.get(); break
            except Exception:
                pass
            time.sleep(2)
        if result is None:
            raise RuntimeError(f"no 3D model within {TWIN_CAP_S} s")
        for k in ("model_mesh", "model_glb", "pbr_model", "base_model", "model"):
            v = result.get(k)
            u = v.get("url") if isinstance(v, dict) else (v if isinstance(v, str) else None)
            if u and str(u).startswith("http"):
                return httpx.get(u, timeout=180).content
        raise RuntimeError(f"no mesh in the answer ({sorted(result.keys())})")

    def run_plan_web(self) -> None:
        """Tavily finds guides, Claude reads them and writes one procedure: the first plan, fast."""
        from training import label, web_scrape

        t0 = time.time(); self.set("plan_web", "running")
        try:
            cfg = self.cfg()
            pages = web_scrape.scrape(cfg, self.dir / "web", max_results=4)
            if not pages:
                raise RuntimeError("no readable repair guides found")
            client = _claude()
            futs = [_pool.submit(label.procedure_from_web, client, cfg, p) for p in pages[:6]]
            guides = {}
            for p, f in zip(pages[:6], futs):
                try:
                    ex = f.result(timeout=90)
                    if ex.is_repair_guide and ex.procedure.steps:
                        guides[p["url"]] = ex.procedure
                except Exception as e:
                    print(f"guide {p['url']}: {str(e)[:100]}", flush=True)
            if not guides:
                raise RuntimeError(f"{len(pages)} pages read, none was a step-by-step guide for this repair")
            proc = next(iter(guides.values())) if len(guides) == 1 else label.merge_procedures(client, cfg, guides)
            srcs = [{"title": p.get("title") or p["url"], "url": p["url"], "kind": "web"} for p in pages if p["url"] in guides]
            self._install_plan(proc, srcs, "web guides")
            self.set("plan_web", "ready", guides=len(guides), steps=len(proc.steps)); self.record("object_plan_web", t0, True)
            _media_pool.submit(self._guard, "pictures", self.run_pictures)
        except Exception as e:
            self.set("plan_web", "failed", error=str(e)[:200]); self.record("object_plan_web", t0, False)

    def run_plan_video(self) -> None:
        """Gemini watches the two most relevant public YouTube tutorials (by URL, nothing downloaded); Claude turns each
        into a procedure and merges them with the web plan. Slow (minutes): the plan is replaced in place when it lands."""
        from google import genai
        from google.genai import types

        from training import label, youtube_labels

        t0 = time.time(); self.set("plan_video", "running")
        try:
            cfg = self.cfg()
            urls = youtube_labels.discover(cfg, 2)
            if not urls:
                raise RuntimeError("no YouTube tutorial found")
            self.set("plan_video", "running", videos=urls)
            gem = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=300_000))
            futs = [_pool.submit(youtube_labels.label_video, gem, cfg, u) for u in urls]
            procs = {}
            client = _claude()
            for u, f in zip(urls, futs):
                try:
                    labels, model = f.result(timeout=480)
                    relevant = [w for w in labels.windows if w.label.relevant]
                    print(f"video {u}: tutorial={labels.is_repair_tutorial} windows={len(labels.windows)} relevant={len(relevant)} ({model})", flush=True)
                    if not labels.is_repair_tutorial and len(relevant) < 3:
                        continue
                    segs = [{"start": w.start_s, "end": w.end_s, "transcript": w.said, "label": w.label.model_dump()} for w in labels.windows]
                    procs[u] = label.build_procedure(client, cfg, youtube_labels.video_id(u) or u, segs)
                except Exception as e:
                    print(f"video {u}: {str(e)[:120]}", flush=True)
            if not procs:
                raise RuntimeError("no video was a tutorial for this repair")
            merged_in = dict(procs)
            if self.plan and self.plan_source == "web guides":
                from training.schemas import Procedure, ProcedureStep

                merged_in["web guides"] = Procedure(object=cfg["object"], fault=cfg["fault"], tools_needed=self.plan.get("tools_needed", []), parts=[],
                                                    steps=[ProcedureStep(index=s["index"], title=s.get("title") or "", instruction=s["instruction"], parts=s.get("parts", []),
                                                                         tools=[], expected_visual_state=s["expected_visual_state"], common_mistakes=s.get("common_mistakes", []),
                                                                         safety_notes=[], start_s=0, end_s=0) for s in self.plan["steps"]], notes="")
            proc = next(iter(merged_in.values())) if len(merged_in) == 1 else label.merge_procedures(client, cfg, merged_in)
            srcs = [{"title": "YouTube tutorial", "url": u, "kind": "youtube"} for u in procs] + [s for s in (self.plan or {}).get("sources", []) if s.get("kind") == "web"]
            self._install_plan(proc, srcs, "web guides + YouTube")
            self.set("plan_video", "ready", videos=len(procs), steps=len(proc.steps)); self.record("object_plan_video", t0, True)
        except Exception as e:
            self.set("plan_video", "failed", error=str(e)[:200]); self.record("object_plan_video", t0, False)

    def run_pictures(self) -> None:
        """One fal picture per step: the product photo edited to show that step's action. Shown when ready, never awaited."""
        import fal_client
        import httpx

        if not (self.dir / "photo.jpg").exists() or not self.plan or not os.getenv("FAL_KEY"):
            self.set("pictures", "skipped", why="no photo, no plan or no FAL_KEY"); return
        t0 = time.time(); self.set("pictures", "running")
        try:
            url = fal_client.upload((self.dir / "photo.jpg").read_bytes(), "image/jpeg")
            name = self.intake.model or self.intake.object
            for s in self.plan["steps"][:MAX_STEP_PICTURES]:
                prompt = (f"This is a product photo of a {name}. Edit it to show this repair step being done on it right now: "
                          f"\"{s['instruction']}\" Show a person's hands and the tool the step needs, doing exactly that. Keep the product, "
                          "its colours, angle and lighting exactly as in the photo. Photorealistic. No text, arrows or labels.")
                try:
                    r = fal_client.subscribe(EDIT_MODEL, arguments={"prompt": prompt, "image_urls": [url]})
                    img = (r.get("images") or [r.get("image")])[0]["url"]
                    out = self.dir / f"{self.fault_slug}-step{s['index']:02d}.png"; out.write_bytes(httpx.get(img, timeout=60).content)
                    with self.lock:
                        self.pictures[s["index"]] = f"/data/objects/{self.slug}/{out.name}"
                except Exception as e:
                    print(f"step picture {s['index']}: {str(e)[:100]}", flush=True)
            self.set("pictures", "ready", n=len(self.pictures)); self.record("object_step_picture", t0, True)
        except Exception as e:
            self.set("pictures", "failed", error=str(e)[:200]); self.record("object_step_picture", t0, False)

    def _install_plan(self, proc, sources: list[dict], source_label: str) -> None:
        """The procedure as the RepairPlan dict the viewer, the coach and the voice already understand."""
        from backend.contracts import RepairPlan, RepairStep, SceneCommand, SourceRef
        from backend.coach import _latest, _plans

        name = self.intake.model or self.intake.object
        refs = [SourceRef(title=s["title"][:120], url=s["url"], kind="youtube" if s.get("kind") == "youtube" else "web") for s in sources[:8]]
        steps = []
        for i, s in enumerate(proc.steps, 1):
            steps.append(RepairStep(index=i, title=(s.title or "")[:40] or None, instruction=s.instruction.strip(), parts=s.parts[:4],
                                    expected_visual_state=s.expected_visual_state.strip(), common_mistakes=(s.common_mistakes + s.safety_notes)[:3],
                                    scene3d=[SceneCommand(op="show_step")], sources=refs[:2], kind="other"))
        plan = RepairPlan(object=name, fault=self.intake.fault or self.first_text, difficulty="medium", time_min=max(5, 3 * len(steps)),
                          tools_needed=proc.tools_needed[:6], safety_notes=[], steps=steps, sources=refs, generated=True,
                          title=f"{name}: {self.intake.fault}"[:60] if self.intake.fault else name, diagnosis=proc.notes[:300] if proc.notes else "",
                          target_key=None, fault_signature=f"object-{self.slug}")
        d = plan.model_dump()
        d["generic"] = True
        d["object_model"] = name
        with self.lock:
            self.plan, self.plan_source = d, source_label
        plan_id = hashlib.sha1(f"{self.slug}{time.time()}".encode()).hexdigest()[:10]
        _plans[self.session] = {"broken_key": None, "plan": d, "parts_card": None, "plan_id": plan_id, "generic": True,
                                "object": name, "photo_url": self.photo_url, "twin_url": self.twin_url}
        _latest.setdefault(self.session, {})["plan_id"] = plan_id
        (self.dir / f"plan-{self.fault_slug}.json").write_text(json.dumps(d, indent=1), encoding="utf-8")

    def _guard(self, phase: str, fn) -> None:
        try:
            fn()
        except Exception as e:   # a phase never kills the job; the status says what happened
            self.set(phase, "failed", error=str(e)[:200])

    # -- entry points
    def start(self, text: str) -> dict:
        self.set("intake", "running")
        if KEYBOARD_RE.search(text):       # the keyboard has its own flow: no photo, no twin, no questions from here
            self.intake = Intake(object="mechanical keyboard", model="", fault="", is_keyboard=True)
            self.set("intake", "ready")
            return {"say": "", **self.public()}
        try:
            self.intake = self.run_intake(text)
        except Exception as e:
            self.set("intake", "failed", error=str(e)[:200])
            return {"say": "I didn't catch the object. Tell me what it is and the exact model.", **self.public()}
        self.set("intake", "ready")
        if self.intake.is_keyboard:
            return {"say": "", **self.public()}        # the viewer hands over to the keyboard flow
        self.slug = canonical_slug(slug_of(self.intake.model or self.intake.object))      # the object; the fault picks the plan inside it
        self.fault_slug = slug_of(self.intake.fault or self.first_text)
        self.dir = OBJECTS / self.slug; self.dir.mkdir(parents=True, exist_ok=True)
        if self.intake.model_question and not self.intake.model:
            return {"say": self.intake.model_question, **self.public()}
        if self._reuse_disk():          # the same object and fault was learned earlier: a real result, served at once
            self._launch(videos_only=True)
            return {"say": self._fresh(f"The {self.intake.model or self.intake.object}: I've already learned this repair. "
                                       f"{len(self.plan['steps'])} steps. Say start when you're ready."), **self.public()}
        self._launch()
        return {"say": self._fresh(self._opening()), **self.public()}

    def _matching_plan(self) -> Path | None:
        """A plan written earlier for this object whose fault reads like this one (6 of 10 content words shared)."""
        want = _words(self.intake.fault or self.first_text)
        best, score = None, 0.0
        for f in self.dir.glob("plan-*.json"):
            try:
                stored = _words(json.loads(f.read_text(encoding="utf-8")).get("fault", ""))
            except Exception:
                continue
            if not want or not stored:
                continue
            sc = len(want & stored) / max(len(want), len(stored))
            if sc > score:
                best, score = f, sc
        return best if score >= 0.6 else None

    def _reuse_disk(self) -> bool:
        """What earlier live runs left for this object: the photo, specs and twin always (they don't depend on the fault);
        a plan and its step pictures only when the fault matches one learned before."""
        d = self.dir
        match = self._matching_plan()
        if not match and not (d / "photo.jpg").exists():
            return False
        try:
            from backend.coach import _latest, _plans

            if (d / "photo.jpg").exists():
                self.photo_url = f"/data/objects/{self.slug}/photo.jpg"; self.set("photo", "ready", cached=True)
            if (d / "specs.json").exists():
                self.specs = json.loads((d / "specs.json").read_text(encoding="utf-8")).get("specs", [])
            if (d / "model.glb").exists():
                self.twin_url = f"/data/objects/{self.slug}/model.glb"; self.set("twin", "ready", cached=True)
            if not match:
                return False                     # photo and twin reused; the plan for this fault is learned now
            self.fault_slug = match.stem[len("plan-"):]
            plan = json.loads(match.read_text(encoding="utf-8"))
            for f in sorted(d.glob(f"{self.fault_slug}-step*.png")):
                self.pictures[int(f.stem.rsplit("step", 1)[1])] = f"/data/objects/{self.slug}/{f.name}"
            self.set("pictures", "ready" if self.pictures else "pending", n=len(self.pictures))
            with self.lock:
                self.plan, self.plan_source = plan, "an earlier run"
            plan_id = hashlib.sha1(f"{self.slug}{time.time()}".encode()).hexdigest()[:10]
            _plans[self.session] = {"broken_key": None, "plan": plan, "parts_card": None, "plan_id": plan_id, "generic": True,
                                    "object": plan.get("object_model") or plan["object"], "photo_url": self.photo_url, "twin_url": self.twin_url}
            _latest.setdefault(self.session, {})["plan_id"] = plan_id
            self.set("plan_web", "ready", cached=True)
            return True
        except Exception as e:
            print(f"reuse {d}: {str(e)[:100]}", flush=True)
            return False

    def answer(self, text: str) -> dict:
        """An answer to the current question. Model answers restart the intake; the rest sharpen the fault."""
        if not self.intake:
            return self.start(text)
        if self.intake.model_question and not self.intake.model:
            merged = f"{self.first_text}. The model: {text}"
            self.first_text = merged
            return self.start(merged)
        if self.intake.fault_question and not self.intake.fault:
            self.intake.fault = text.strip()
            self._launch()
            return {"say": self._fresh(self._opening()), **self.public()}
        self.answers.append(text.strip()); self.asked += 1
        q = self.next_question()
        if self.plan:
            return {"say": self._fresh(self._react(text, "", ready=True)), **self.public()}
        if q:
            return {"say": self._fresh(self._react(text, q)), **self.public()}
        return {"say": self._fresh(self._waiting_line()), **self.public()}

    def _react(self, answer: str, next_q: str, ready: bool = False) -> str:
        """One spoken turn, written by Claude from the conversation so far: a real reaction to the answer, then the next
        question (or 'the steps are ready'). Falls back to a plain line if the model is slow."""
        import anthropic

        name = self.intake.model or self.intake.object
        hist = "\n".join(f"Spanner: {s}" for s in self.said[-6:])
        remaining = [q for q in self.intake.questions[self.asked:] if q != next_q]
        told = any("say start" in x.lower() for x in self.said)
        goal = (("Just react; they already know to say start, so do not tell them again." if told else
                 "Tell them the steps are ready and to say start when they're ready.") if ready else
                f"Then ask this question, in your own words: {next_q}" +
                (f" If they have in fact already answered it, ask instead the first of these that they haven't: {remaining}" if remaining else ""))
        try:
            r = anthropic.Anthropic(timeout=8, max_retries=0).messages.create(
                model=INTAKE_MODEL, max_tokens=90,
                system=(f"You are Spanner, a repair coach on a voice call. The person is fixing a {name} ({self.intake.fault}). "
                        "Reply with ONE or two short spoken sentences: first react to what they just said like a good technician would "
                        "(what it suggests, briefly), never a bare 'got it'. " + goal + " Never repeat a sentence you already said. "
                        "Never say you are checking, consulting, reading or looking up a manual, guide or instructions, and never announce what "
                        "you are about to look up: the screen already shows the research. Do not make a point you already made, "
                        "even in other words. "
                        "No lists, no markdown.\n\nWhat you already said:\n" + (hist or "(nothing yet)")),
                messages=[{"role": "user", "content": answer}])
            out = "".join(b.text for b in r.content if b.type == "text").strip()
            from .speech import clean_line

            out = clean_line(out, self.said)       # no "let me consult the manual", no old point in new words
            if out:
                return out
        except Exception as e:
            print(f"react: {str(e)[:80]}", flush=True)
        return ("The steps are ready. Say start when you're ready." if ready else f"Thanks. {next_q}")

    def _fresh(self, line: str) -> str:
        """Never the same sentence twice in one flow: a repeat becomes a progress line, or a short variant."""
        if line in self.said:
            line = self._progress_line() if self.intake else "Still here."
            if line in self.said:
                for alt in ("Right.", "Okay.", "Still on it.", "One moment."):
                    if alt not in self.said:
                        line = alt; break
        self.said.append(line)
        return line

    def _progress_line(self) -> str:
        """What is done and what is still running, from the real phase states; different every time the state changes."""
        p = self.phase
        done = [n for n, k in (("the photo", "photo"), ("the 3D twin", "twin"), ("the guides", "plan_web")) if p[k]["status"] == "ready"]
        running = [n for n, k in (("the twin", "twin"), ("the guides", "plan_web"), ("two repair videos", "plan_video")) if p[k]["status"] == "running"]
        name = self.intake.model or self.intake.object
        parts = []
        if done:
            parts.append(f"I have {', '.join(done)} for the {name}.")
        if running:
            parts.append(f"Still working on {' and '.join(running)}.")
        if self.plan and not self.intake.questions[self.asked:]:
            parts.append("Say start when you're ready.")
        return " ".join(parts) or f"Still on the {name}."

    def _launch(self, videos_only: bool = False) -> None:
        if not videos_only:
            if self.phase["plan_web"]["status"] != "pending":
                return
            if self.phase["photo"]["status"] == "pending":
                _media_pool.submit(self._guard, "photo", self.run_photo)
            elif self.phase["twin"]["status"] == "pending" and self.photo_url:
                _media_pool.submit(self._guard, "twin", self.run_twin)          # photo reused from an earlier run, twin still missing
            _pool.submit(self._guard, "plan_web", self.run_plan_web)
        elif self.phase["photo"]["status"] == "pending":                 # plan reused, but no photo yet: fetch it (the twin follows)
            _media_pool.submit(self._guard, "photo", self.run_photo)
        elif self.phase["twin"]["status"] == "pending" and self.photo_url:
            _media_pool.submit(self._guard, "twin", self.run_twin)
        if os.getenv("GEMINI_API_KEY") and not self.skip_video:
            _pool.submit(self._guard, "plan_video", self.run_plan_video)
        else:
            self.set("plan_video", "skipped", why="no GEMINI_API_KEY")

    def _opening(self) -> str:
        name = self.intake.model or self.intake.object
        q = self.next_question()
        return f"Okay, the {name}." + (f" While I get that ready: {q}" if q else " Give me a moment.")

    def _waiting_line(self) -> str:
        p = self.phase
        if p["plan_web"]["status"] == "running":
            return self._progress_line()
        if p["plan_web"]["status"] == "failed" and p["plan_video"]["status"] == "running":
            return "Thanks. The written guides were no good; I'm watching two repair videos instead, give me a minute."
        if p["plan_web"]["status"] == "failed" and p["plan_video"]["status"] != "running":
            return "I couldn't find a usable repair guide for that. Tell me the fault in different words and I'll try again."
        return "Thanks."


_STOP = {"the", "a", "an", "is", "are", "my", "it", "its", "of", "to", "and", "or", "than", "one", "very", "much", "on", "in",
         "has", "have", "does", "doesnt", "not", "any", "more", "will", "wont", "be", "been", "with", "that", "this"}


def _words(text: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split() if len(w) > 2 and w not in _STOP}


def _claude():
    import anthropic

    return anthropic.Anthropic(timeout=120, max_retries=1)


# ---------------------------------------------------------------- endpoints
class ObjectReq(BaseModel):
    session: str
    text: str
    fast: bool = False          # skip the slow video-watching pass (used by scripts/warm_objects.py)


@router.post("/object")
async def start_object(req: ObjectReq):
    text = req.text.strip()[:300]
    cur = _jobs.get(req.session)
    if cur and cur.intake and time.time() - cur.t0 < 90 and not cur.intake.is_keyboard:
        if _words(text) & _words(cur.first_text) or cur.next_question():   # the same request again, or an answer: join the running job
            return await asyncio.to_thread(cur.answer, text) if cur.next_question() and not (_words(text) & _words(cur.first_text)) \
                else {"say": cur._fresh(cur._progress_line()), **cur.public()}
    job = Job(req.session, text)
    job.skip_video = req.fast
    _jobs[req.session] = job
    return await asyncio.to_thread(job.start, job.first_text)


@router.post("/object/answer")
async def answer_object(req: ObjectReq):
    job = _jobs.get(req.session)
    if not job:
        raise HTTPException(404, "no object job for this session")
    return await asyncio.to_thread(job.answer, req.text.strip()[:300])


@router.get("/object")
async def object_status(session: str):
    job = _jobs.get(session)
    if not job:
        return {"session": session, "phases": {}, "plan_ready": False}
    return job.public()
