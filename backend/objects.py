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
TWIN_MODEL = os.getenv("FAL_TWIN_MODEL", "fal-ai/trellis")
EDIT_MODEL = os.getenv("FAL_EDIT_MODEL", "fal-ai/nano-banana/edit")
TWIN_CAP_S = int(os.getenv("FAL_TWIN_CAP_S", "240"))
MAX_STEP_PICTURES = int(os.getenv("OBJECT_STEP_PICTURES", "6"))
FALLBACK_BETA = "server-side-fallback-2026-07-01"

router = APIRouter(prefix="/coach", tags=["objects"])
_pool = ThreadPoolExecutor(max_workers=8)
_jobs: dict[str, "Job"] = {}


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
def slug_of(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]
    return s or hashlib.sha1(text.encode()).hexdigest()[:10]


class Job:
    def __init__(self, session: str, text: str):
        self.session, self.first_text = session, text
        self.answers: list[str] = []
        self.intake: Intake | None = None
        self.asked = 0
        self.slug = ""
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
            path = self._download_image(self._image_candidates(name))
            self.photo_url = f"/data/objects/{self.slug}/{path.name}"
            self.set("photo", "ready", url=self.photo_url); self.record("object_photo", t0, True)
            _pool.submit(self._guard, "twin", self.run_twin)
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
                    path = self._download_image([pick.image_url] + pick.image_alternatives)
                    self.photo_url = f"/data/objects/{self.slug}/{path.name}"
                    self.set("photo", "ready", url=self.photo_url); _pool.submit(self._guard, "twin", self.run_twin)
                except Exception as e:
                    self.set("photo", "failed", error=str(e)[:200])
            self.specs = [s.model_dump() for s in pick.specs][:6]
            (self.dir / "specs.json").write_text(json.dumps({"product_name": pick.product_name, "official_product_url": pick.official_url,
                                                             "specs": self.specs, "sources": sources[:8]}, indent=1), encoding="utf-8")
            self.record("object_specs", t0, True)
        except Exception as e:
            print(f"specs {name}: {str(e)[:120]}", flush=True); self.record("object_specs", t0, False)

    def _image_candidates(self, name: str) -> list[str]:
        """Tavily image search: the product on a plain background (Linkup's structured answers rarely carry image URLs)."""
        try:
            from backend.shop import _tavily

            res = _tavily().search(f"{name} product photo white background", include_images=True, max_results=5)
            imgs = [i if isinstance(i, str) else i.get("url", "") for i in res.get("images", [])]
            return [u for u in imgs if u][:6]
        except Exception as e:
            print(f"tavily images: {str(e)[:100]}", flush=True)
            return []

    def _download_image(self, urls: list[str]) -> Path:
        import io

        import httpx
        from PIL import Image

        last = "no image url"
        for u in urls[:8]:
            if not str(u).startswith("http"):
                continue
            try:
                r = httpx.get(u, timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Spanner repair coach)"})
                r.raise_for_status()
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                if img.width < 300 or img.height < 300:
                    last = f"too small: {u}"; continue
                img.thumbnail((1600, 1600))
                out = self.dir / "photo.jpg"; img.save(out, "JPEG", quality=90)
                return out
            except Exception as e:
                last = f"{u}: {str(e)[:80]}"
        raise RuntimeError(f"no usable product photo ({last})")

    def run_twin(self) -> None:
        """fal image-to-3D of the product photo. Explicit queue polling with a cap: the queue host dropped for long
        stretches on the venue network tonight, and the demo must not hang on it."""
        import fal_client
        import httpx

        t0 = time.time(); self.set("twin", "running")
        try:
            url = fal_client.upload((self.dir / "photo.jpg").read_bytes(), "image/jpeg")
            handle = fal_client.submit(TWIN_MODEL, arguments={"image_url": url})
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
            m = result.get("model_mesh") or result.get("model_glb") or {}
            glb = httpx.get(m["url"], timeout=120).content
            (self.dir / "model.glb").write_bytes(glb)
            self.twin_url = f"/data/objects/{self.slug}/model.glb"
            self.set("twin", "ready", url=self.twin_url, bytes=len(glb)); self.record("object_twin", t0, True)
        except Exception as e:
            self.set("twin", "failed", error=str(e)[:200]); self.record("object_twin", t0, False)

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
            _pool.submit(self._guard, "pictures", self.run_pictures)
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
                    out = self.dir / f"step{s['index']:02d}.png"; out.write_bytes(httpx.get(img, timeout=60).content)
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
        (self.dir / "plan.json").write_text(json.dumps(d, indent=1), encoding="utf-8")

    def _guard(self, phase: str, fn) -> None:
        try:
            fn()
        except Exception as e:   # a phase never kills the job; the status says what happened
            self.set(phase, "failed", error=str(e)[:200])

    # -- entry points
    def start(self, text: str) -> dict:
        self.set("intake", "running")
        try:
            self.intake = self.run_intake(text)
        except Exception as e:
            self.set("intake", "failed", error=str(e)[:200])
            return {"say": "I didn't catch the object. Tell me what it is and the exact model.", **self.public()}
        self.set("intake", "ready")
        if self.intake.is_keyboard:
            return {"say": "", **self.public()}        # the viewer hands over to the keyboard flow
        self.slug = slug_of(f"{self.intake.model or self.intake.object}-{self.intake.fault}")
        self.dir = OBJECTS / self.slug; self.dir.mkdir(parents=True, exist_ok=True)
        if self.intake.model_question and not self.intake.model:
            return {"say": self.intake.model_question, **self.public()}
        if self._reuse_disk():          # the same object and fault was learned earlier: a real result, served at once
            self._launch(videos_only=True)
            return {"say": f"The {self.intake.model or self.intake.object}: I've already learned this repair. "
                           f"{len(self.plan['steps'])} steps. Say start when you're ready.", **self.public()}
        self._launch()
        return {"say": self._opening(), **self.public()}

    def _reuse_disk(self) -> bool:
        """Everything a previous live run of this exact object + fault left under data/objects/<slug>/."""
        d = self.dir
        if not (d / "plan.json").exists():
            return False
        try:
            from backend.coach import _latest, _plans

            plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
            if (d / "photo.jpg").exists():
                self.photo_url = f"/data/objects/{self.slug}/photo.jpg"; self.set("photo", "ready", cached=True)
            else:
                self.set("photo", "failed", error="no photo from the earlier run")
            if (d / "specs.json").exists():
                self.specs = json.loads((d / "specs.json").read_text(encoding="utf-8")).get("specs", [])
            if (d / "model.glb").exists():
                self.twin_url = f"/data/objects/{self.slug}/model.glb"; self.set("twin", "ready", cached=True)
            else:
                self.set("twin", "pending")
            for f in sorted(d.glob("step*.png")):
                self.pictures[int(f.stem[4:])] = f"/data/objects/{self.slug}/{f.name}"
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
            return {"say": self._opening(), **self.public()}
        self.answers.append(text.strip()); self.asked += 1
        q = self.next_question()
        if self.plan:
            return {"say": "Thanks. The steps are ready. Say start when you're ready.", **self.public()}
        if q:
            return {"say": "Got it. " + q, **self.public()}
        return {"say": self._waiting_line(), **self.public()}

    def _launch(self, videos_only: bool = False) -> None:
        if not videos_only:
            if self.phase["photo"]["status"] != "pending":
                return
            _pool.submit(self._guard, "photo", self.run_photo)
            _pool.submit(self._guard, "plan_web", self.run_plan_web)
        elif self.phase["twin"]["status"] == "pending" and self.photo_url:
            _pool.submit(self._guard, "twin", self.run_twin)
        if os.getenv("GEMINI_API_KEY"):
            _pool.submit(self._guard, "plan_video", self.run_plan_video)
        else:
            self.set("plan_video", "skipped", why="no GEMINI_API_KEY")

    def _opening(self) -> str:
        name = self.intake.model or self.intake.object
        q = self.next_question()
        return f"Okay, the {name}. I'm finding it and reading the repair guides now." + (f" Meanwhile: {q}" if q else "")

    def _waiting_line(self) -> str:
        p = self.phase
        if p["plan_web"]["status"] == "running":
            return "Thanks. I'm still reading the guides, a few more seconds."
        if p["plan_web"]["status"] == "failed" and p["plan_video"]["status"] == "running":
            return "Thanks. The written guides were no good; I'm watching two repair videos instead, give me a minute."
        if p["plan_web"]["status"] == "failed" and p["plan_video"]["status"] != "running":
            return "I couldn't find a usable repair guide for that. Tell me the fault in different words and I'll try again."
        return "Thanks."


def _claude():
    import anthropic

    return anthropic.Anthropic(timeout=120, max_retries=1)


# ---------------------------------------------------------------- endpoints
class ObjectReq(BaseModel):
    session: str
    text: str


@router.post("/object")
async def start_object(req: ObjectReq):
    job = Job(req.session, req.text.strip()[:300])
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
