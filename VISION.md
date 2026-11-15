# Spanner — Vision

_Agreed with the team on 2026-11-14 (Imperial AI Hackathon, 14–15 Nov 2026)._

> Every Claude Code session on this project reads this file first.
> Re-read it before each major step. If the work needs to deviate from it, stop and ask the team; then update this file. Never change the vision silently.

---

## Update 2026-11-15 04:30: Spanner for any object, live (agreed with Alberto)

**Goal.** The first screen asks what to fix: the object and its exact model ("AirPods Pro 2") and what is wrong. From
that moment Spanner works live for whatever was named: it finds the best product photo and the specs with Linkup,
builds a 3D twin of the object with fal (one solid mesh first; detailed twins like the keyboard come later), and learns
the repair from the web (Tavily guides, fast) and from YouTube (Gemini watches public videos, Claude writes the
procedure, slower, merged in when it lands). While it builds, Spanner asks real diagnostic questions, one at a time,
and the answers sharpen the fault. Then it guides the repair with the camera watching, as for the keyboard.

**Who it's for.** The judge who names an object Spanner has never seen; the person with that object in hand.

**End result.** Say "AirPods Pro 2, the right earbud is quiet": the product photo and specs appear in seconds; Spanner
asks "since when?", "did you try cleaning the mesh?"; a sourced step plan is ready in about two minutes and read out;
the twin replaces the photo when fal finishes; each step shows a card with a fal-made picture of that action on the
object, the voice narrates, the camera follows progress and flags mistakes; "show me replacements" shops for that model.
The keyboard keeps its hand-built, part-level hologram and animations.

**Key decisions.** Live for any object, nothing pre-built (a test run on AirPods Pro 2 is a real run whose results
stay as a real cache). Steps on generated twins = twin + step picture + voice (no part animations: fal's mesh has no
parts). Web guides give the first plan fast; YouTube refines it when ready. No mocks anywhere; an honest "still reading
two guides" when the person's questions run out before the plan.

**Defaults chosen.** Haiku for the intake and the questions; Sonnet for the plan; Trellis on fal for the twin with a
4-minute cap and explicit queue polling; the photo stands in for the twin until then; every stage timed into Databricks.

**Out of scope.** Part-level animation on generated twins; fine-tuning for other objects; the F-row keyboard model
(still unknown: RK84?).

**Build steps.** 1 object job (intake, photo+specs, web plan, status) → 2 viewer object mode → 3 fal twin + GLB →
4 question loop → 5 YouTube deep path → 6 step pictures → 7 live run on a second object. Cut in that reverse order if
time runs out. Plan: `C:\Users\alber\.claude\plans\frolicking-dreaming-wind.md`.

---

## Update 2026-11-15: track switch (agreed with Alberto)
- **Main track is now Voice, Vision & Generative Media** (team decision; bounties are gated, so the Databricks / Linkup / Tavily bounties are no longer contested; those layers stay in the product, not in the pitch's lead). Adaption and Figma are open to all and stay.
- **fal:** the first use (redrawing the user's camera frame with the step done, then animating it) was built, tested and **dropped by Alberto on 15 Nov**. A different use of fal is to be decided.

---

## Goal
Build **Spanner**, an iPhone app that lets anyone fix simple broken things by pointing their camera at them. Spanner sees what's wrong, researches how to fix it, then guides the user through the repair in real time, Jarvis-style: overlays on the live camera, 3D animations of the parts, generated step illustrations, and a voice that talks them through it, listens to their questions, knows how far they've got, and warns them when they make a mistake.
When a part has to be replaced, a **Linkup research agent** finds the exact replacement for the user's model, checks that it fits, compares prices, pulls the official manual's specs and finds tutorials, **citing a source for every claim**. All the knowledge and data behind Spanner lives on **Databricks**.
The goal for the weekend is a **live, reliable 2-minute demo** on a real object that **wins at least one prize**. We compete in the **Autonomous Agents** main track and go hard for the **Databricks**, **Linkup** and **Tavily** bounties, plus the open **Figma** prize (and Adaption if the fine-tune is ready). Every sponsor does a real job; nothing is bolted on.

## Who it's for
- **Users:** everyday people facing an easy repair (a bike chain that came off, a loose part, a simple fix) who don't know the part names and find text guides and long YouTube videos hard to follow. They need to be **shown** which part is which and what to do with it, step by step, hands-free.
- **The judges:** they need to grasp the value in seconds ("Jarvis for repairs"), see the agent *acting* (looking, deciding, drawing, talking, checking), and believe it works live.
- **The team (4 people):** the code is **vibe-coded**. Each person runs Claude Code on their own part; the humans review diffs, test, and steer. Code quality matters less than a working, reviewable demo.

## Prizes we're going for (and how we win each)
| Prize | Their brief | Spanner's answer (must be visible in the demo or pitch) |
|---|---|---|
| **Main track: Autonomous Agents** (£500 / £200) | Autonomous agents | Spanner looks, decides, researches, acts (HUD, 3D, voice) and checks the result on its own, step after step |
| **Databricks bounty** (£960 + swag) | A pipeline, agent or app on the Databricks Data Intelligence Platform; scored on **creativity, technical depth, real-world impact** | Databricks is **where Spanner learns**, and it sits **inside the live loop**. Unity Catalog + Volumes; a Lakeflow medallion pipeline with **quality checks on Claude's labels**; a **Vector Search** knowledge base the brain queries every step; a **learning loop** (mistakes caught live in 2+ sessions are written back into the knowledge base, so the next user is warned); the **repair-or-replace impact** table (money and e-waste saved); the **Adaption before/after scoreboard**; **MLflow** runs and Claude traces; **AI/BI dashboard + Genie** for judges' questions. All deployed from one Asset Bundle |
| **Best use of Linkup** (£560 + credits) | An agent built around Linkup live web search that **researches, verifies or acts on fresh information, citing its sources** | The **replacement-part agent**. *Researches:* offers, price, stock, the official manual, tutorials. *Verifies:* fit for the exact model on 2+ independent sites, plus a safety-recall check limited to the last 2 years. *Acts:* the brain calls it as a tool when a part is needed, and it makes a **repair-or-replace decision** for the user (part cost vs a new unit, money and e-waste saved). Every claim links to its source; citation coverage is measured |
| **Best Use of Adaption** (open to all; I own this) | Adaption | The **step tracker**, Spanner's most frequent live decision (*not started / in progress / done / mistake?*), fine-tuned with Adaption on **real data**: examples derived automatically from Claude-labelled repair videos and procedure time ranges, curated in Databricks, cleaned and expanded in Adaptive Data, trained with AutoScientist. Shown as a **before/after** vs Claude Haiku (accuracy, mistake recall, latency) in MLflow and on the Databricks dashboard |
| **Best use of Tavily** (credits) | Best use of the Tavily search API | **Builds the knowledge base offline**: searches and extracts repair guides, which Claude turns into procedures that flow into Databricks. Distinct job from Linkup (live, user-facing) |
| **Best-Designed AI Product** (Figma, £960, open to all) | Design quality | HUD and flows designed in Figma first, built from the design |

**Rule:** Linkup and Tavily never do the same job. **Linkup = live, fresh, verified, user-facing. Tavily = offline knowledge-base building.** Say it in exactly these words in the pitch.
**To confirm with the organisers:** whether bounties are only open to teams in their main track (Databricks, Linkup and Tavily sit under Autonomous Agents).

## End result
**The demo flow (what must work on stage):**
1. The user opens Spanner on an iPhone and taps **Start repair**.
2. They point the camera at the broken object. Jarvis-style **HUD boxes and labels** lock onto the parts within about a second, and update live as the camera moves.
3. Spanner **says out loud** what the object is and what's wrong ("Rear derailleur. The chain has come off the smallest cog.").
4. A **"researching"** animation shows sources appearing (iFixit, YouTube, the web). Spanner builds a step-by-step plan.
5. Guidance begins. For each step:
   - a **step card** shows "Step 2/5", the instruction, a progress ring, and a **fal-generated blueprint illustration** (or a short looping clip for the demo repair);
   - a **3D panel** shows the object's model pulling apart, with the relevant part glowing, rotating to the right angle;
   - **HUD arrows and labels** on the live camera point at the real part;
   - Spanner **talks** the user through it in at most 2 short sentences.
6. The user can **hold to talk** at any moment ("Wait, which way do I turn the pedal?") and gets a spoken answer in under 5 seconds, with the 3D model and HUD updating to show it.
7. Spanner **tracks progress**: it moves to the next step when the user says "done" and the camera agrees, or when the camera clearly sees the step is complete.
8. If the user makes a **mistake**, a red alert banner appears, the phone vibrates, and Spanner says what's wrong.
9. **The part must be replaced** (the demo fault is chosen so that it does): Spanner asks the user to show the **model label**, reads the exact model, and a **Replacement card** slides in beside the 3D view: the 3 best offers (price, retailer, stock), a **"✓ fits your <model>, 2 sources"** badge (or an honest "unverified"), the **decision** ("Repair it: £18 in parts vs £279 new. Saves £261 and 0.25 kg of e-waste"), any safety recall, the official manual's key spec with its page ("caliper bolt 25 Nm, p. 42") and a tutorial link that jumps to the right chapter. Every claim links to its source. This is the **climax of the demo**.
   Then **30 seconds behind the scenes**: Genie answers "which step do users get wrong most?", the dashboard shows the Adaption step tracker vs Claude (accuracy and latency) and the money and e-waste saved, and the Figma file sits next to the app.
10. When finished, a **summary screen** shows what was fixed, the steps, the time taken and the sources.
11. If the object is plugged into the mains or runs on gas, Spanner **refuses politely** and explains why.

**Behind the scenes (must also be true):**
- Every session is stored in **Databricks**, with a dashboard showing common faults, where users get stuck, and the latency of each stage.
- Spanner's **knowledge** is a Databricks data product (`spanner.training`): repair videos and Tavily web guides become procedures (Claude as the "teacher"), pass Lakeflow quality checks, and are served by a **Vector Search** index the live agent queries (`training/kb.py`). Every Linkup research result (offers, prices, compatibility verdicts, manual specs) lands there too.
- **MLflow** records every training run and traces every Claude call.
- A small model **fine-tuned with Adaption** can replace Claude in Pass 1, with a comparison chart (accuracy and speed vs Claude Haiku) for the pitch.
- The whole interface was **designed in Figma first** and built from those designs.
- A `/debug` web page shows the live pipeline (frame, boxes, observation, report, response, timings) so the team can review it without the phone.

**Look and feel:** Jarvis / holographic HUD. Deep navy background `#05080F`, primary cyan `#00E5FF`, ok `#3DFFA8`, warn `#FFC83D`, danger `#FF3D5A`, glass panels with blur, corner-bracket boxes instead of full rectangles, monospace labels (e.g. JetBrains Mono), clean sans for instructions (e.g. Inter), soft glow, scan-line animation while thinking, boxes that glide rather than jump.

## Key decisions
| Decision | What was agreed | Why |
|---|---|---|
| Product direction | Consumer **easy repairs**, computer vision, real-time explanation (the user's words: "you show it a bike with its chain broken, it identifies what's wrong, scrapes databases or YouTube to understand how to do it, knows where you are in your repair, guides you through the steps with super cool Jarvis-style diagrams, 3D animations and AR, talks you through it, takes audio inputs, alerts if errors") | Chosen after the /devil reviews over the legacy-code agent and the company brain |
| Name | **Spanner** | Chosen by the team |
| Platform | **iOS mobile app** | Team decision |
| App technology | **Expo (React Native) + TypeScript**, run with Expo Go | The team develops on Windows; native Swift needs a Mac |
| Backend | **Python 3.12 + FastAPI**, one service | Same language for the 3 backend people; reuses `starter/` |
| "AR" means | **A HUD drawn over the live camera** (boxes, labels, arrows) + a 3D model in a side panel | A 3D model locked onto the real object is too risky for 20 h |
| Pipeline | Capture → preprocess → detector (CV model) → Claude vision → speech-to-text → **Claude Pass 1** (turns everything into a readable Situation Report) → **Claude Pass 2** (the repair brain) → store in Databricks | The team's design, with the corrections below |
| Two loops | **Fast loop** every frame (preprocess + detect → HUD, under 0.5 s); **slow loop** only on keyframes or speech (Claude vision → Pass 1 → Pass 2 → speech, under 5 s) | The full chain takes 5–10 s; running it on every frame would freeze the HUD |
| Frames, not video | The phone sends **sampled JPEG frames** (about 1 per second), not a video stream | Survives venue Wi-Fi; no model needs video here |
| Transport | **WebSockets** between the app and the backend (team decision, 14 Nov) | The server can push alerts and brain events the moment they're ready |
| Repo layout | Repo root (the `mechanic-agent/` skeleton was dropped on 14 Nov): `backend/` package (`contracts.py`, `config.py`, `session.py`, `frames.py`, ... plus `offline.py`, `parts_agent.py`, `knowledge.py`), `fixtures/`, `tests/`, `training/` (video + web pipeline, Adaption step tracker, evaluations), `databricks/` (Asset Bundle, Lakeflow pipeline, dashboard, Genie), `data/procedures/`, `data/manuals/`. Shared deps in `pyproject.toml` + `uv.lock`; the training packages are an optional group (`uv sync --group training`). Keys in `.env` at the root (never committed) | Team code, 14 Nov |
| Integration contract | The live backend uses the training/sponsor features only through `backend/knowledge.py`, which speaks `backend/contracts.py`: `plan_from_knowledge()` → `RepairPlan`; `search_steps()`; `track_step_async()` → `step_status` in `in_progress / done / mistake` (mistakes logged automatically to the learning loop); `PARTS_TOOL` / `handle_parts_tool()` / `get_parts_card()`. **Needs one contract addition by P3:** `BrainResponse.parts_card: dict \| None` so the app can show the Linkup card | One stable surface, so teammates' files and the training side can change independently |
| Adaption ownership | `backend/config.py` still has `PASS1_BACKEND=adaption` (P3's original Pass 1 plan). The Adaption **entry** is the step tracker (`training/step_tracker.py`); both read the same `TOGETHER_API_KEY` / `ADAPTION_*` settings. P3 keeps Pass 1 on Claude unless both are fine-tuned | Avoids two people training two models for one prize |
| Training from videos | Repair videos are turned into labelled data by Claude (the "teacher"): a procedure knowledge base in `data/procedures/`, a detector dataset, held-out evaluation, and an optional Adaption dataset. Claude itself is not trained | Gives the agent a reference procedure checked against real videos, and fixes the detector's weak spot |
| Main track | **Autonomous Agents** (team decision, 14 Nov) | It holds the Databricks, Linkup and Tavily bounties, which match what's built |
| Demo fault | **Must need a replacement part** (team decision, 14 Nov). Easy and safe to do live, but something is worn or broken and has to be bought | Without a part to buy, the Linkup agent has nothing to show on stage |
| Linkup research agent | `backend/parts_agent.py`: model label → offers (Linkup deep, structured) → compatibility check per offer (Linkup sourced answer + Claude judge, "verified" needs 2+ independent sites) → official manual (PDF or page, Claude extracts specs with pages) → YouTube tutorial links. Runs in the background after diagnosis; results cached | The Linkup bounty asks for research + verification + citations on fresh information; this does exactly that |
| Tavily knowledge-base builder | `python -m training.pipeline web`: Tavily search + extract repair guides → Claude → procedures → Databricks | Offline knowledge without needing videos; a separate job from Linkup |
| YouTube | **Find and link** tutorials (with timestamps when the page gives chapters). **Never download** videos or use them as training data | YouTube's terms; our own filmed videos are the training data |
| Databricks for knowledge | Catalog `spanner`, schema `training` (the live session data stays in `spanner.repair`): bronze tables + Volume, Lakeflow pipeline with expectations (silver/gold), Vector Search index `kb_steps_index`, MLflow experiment `/Shared/spanner-training`, AI/BI dashboard + Genie, Asset Bundle in `databricks/` | Built to score on technical depth and real-world impact |
| Demo object | **Royal Kludge RK61** (hot-swap 60% keyboard), fault: one dead switch (key F), part to buy: an MX-style 3/5-pin switch (team decision, 14 Nov ~17:15) | Real part to buy (Linkup), lots of tutorials (Gemini), easy to model and film, safe live |
| 3D | **Generated in code** (`training/scene3d.py` → `data/models/rk61/`): the true-to-spec RK61 (289×103×39 mm, 61 keys) with 255 named parts (keycaps, switch internals, hot-swap sockets, LEDs, plate cut-outs, PCB, battery, case). `training/viewer/` is the Jarvis hologram: 11-step repair with animated tools, "what can go wrong" demos, x-ray, exploded view, inspector with sourced specs, Linkup parts panel, voice narration + voice commands. P1 ports `applyCommand()`/`applyState()` into the app's 3D panel | No 3D artist or model download needed; every step's 3D comes from the knowledge base |
| YouTube | **Watched, never downloaded:** Gemini labels public tutorials by URL (`training/youtube_labels.py`); only our derived labels + the URL are stored | Learns "where you are in the repair" from real videos without breaking YouTube's terms |
| Talking back | ElevenLabs text-to-speech added | It was missing from the original pipeline |
| Storage | **Databricks** (Delta tables + a Volume for keyframes), written in background batches, SQLite fallback | Sponsor; must never slow the demo |
| Fine-tuning | **Changed 14 Nov:** Adaption fine-tunes the **step tracker** (`training/step_tracker.py`, dataset from `training/adaption_dataset.py`, evaluation `training/eval_step_tracker.py`), not Pass 1. Served on an OpenAI-compatible endpoint (Together); `step_check()` falls back to Claude Haiku on timeout or bad output. Launch training **by 22:00 Saturday**. The model goes live only if it matches Claude's accuracy within 3 points; the before/after chart is the entry either way | Pass 1 trained on Claude-invented data showed nothing; the step tracker is the most frequent live decision, its training data comes from real videos for free, and latency is visible on stage |
| Linkup acts | The parts agent also gives a **repair-or-replace decision** (cheapest verified part vs a new unit's price; repair if ≤ 60%) with money and e-waste saved, and a **safety-recall check** (last 2 years only). The brain starts it through the `find_replacement_part` tool | "Acts on fresh information" is the part of Linkup's brief most entries will miss |
| Learning loop | The brain calls `training.feedback.record_live_mistake()` on every caught mistake → `bronze_live_mistakes` → mistakes seen in 2+ sessions merge into the canonical step's `common_mistakes` → re-indexed → the next user is warned | Databricks makes the product better with use: the creativity argument |
| Generated media | **fal**: step illustrations (text-to-image), a 3D scan of the user's object (image-to-3D), short step clips for the demo repair (image-to-video, made in advance) | Sponsor; media never blocks guidance |
| UI/UX | **Designed in Figma first** (foundations, components, screens, clickable prototype), then built from the design via Figma's MCP server / Dev Mode | Sponsor; the prototype doubles as a pitch storyboard and backup demo |
| Team of 4 | **P1 App** (Expo app + Figma), **P2 Eyes** (preprocess, detector, Claude vision, fal media, repair pack), **P3 Voice** (setup, contracts, server, speech in/out, Pass 1, Adaption), **P4 Brain** (Pass 2, research, safety, Databricks, dashboard) | Parallel work with clear ownership |
| Same stack for everyone | Python deps locked with `uv` (`pyproject.toml` + `uv.lock`), app deps locked with `package-lock.json`; one shared `backend/contracts.py` defines every data shape; TypeScript types generated from it | So 4 people can work in parallel without drifting apart |
| Way of working | Vibe-coded with Claude Code; humans review every diff and run `/code-review` before merging | The team's choice |

## Defaults I chose
- **Claude models:** Haiku 5.5 for vision and Pass 1, Sonnet 5.5 for Pass 2, Opus 5.5 for the one-off research.
- **Detector:** YOLO-World (open vocabulary, prompted with the part names); fallback Florence-2 on fal, or coarse boxes from Claude.
- **Research sources:** the Databricks knowledge base first (Vector Search over procedures from videos, Tavily guides and manuals), then live Linkup research for parts and manuals. Results cached; the demo fault is researched ahead of time.
- **Compatibility rule:** an offer is "verified" only if it fits the exact model on **2+ independent sites** (the retailer's own fitment list counts as one); otherwise the card says "unverified". Links the model didn't receive from Linkup are removed.
- **Voice:** ElevenLabs Scribe (speech-to-text) and ElevenLabs low-latency TTS; push-to-talk; `expo-speech` as fallback.
- **Phone ↔ laptop:** backend on a laptop, exposed with a Cloudflare Tunnel (or ngrok); phone hotspot as backup.
- **WebSockets** carry frames, audio and brain events (changed from the original plain-HTTP design on 14 Nov).
- **Step advance rule:** user says "done" and the camera doesn't contradict it, or the camera matches the expected state with confidence ≥ 0.8 on 2 keyframes in a row.
- **Fault confirmation:** 2 keyframes in a row agree, or the user confirms by voice.
- **Spoken replies:** at most 2 short sentences.
- **Generated images contain no text** (models garble lettering); the app overlays its own labels.
- **Adaption model goes into the demo only if** valid JSON ≥ 98% and intent accuracy is within 3 points of Claude Haiku.
- **Switches:** `USE_MOCKS`, `PASS1_BACKEND`, `FAL_ENABLED`, `STORE_BACKEND`, so any sponsor service can be turned off without breaking the demo.

## Out of scope
- A 3D model tracked and locked onto the real object in space (true anchored AR).
- Swift / native iOS code, Android, user accounts and login.
- Fine-tuning the vision or detection models (only Pass 1 is fine-tuned).
- Generating video live during a repair (clips are made in advance for the demo repair only).
- Continuous video streaming.
- Repairs on anything plugged into the mains or running on gas (Spanner refuses these).
- The ideas the team rejected: the **legacy-code fixing agent** and the **company brain / employee training** product, including the frontline-worker version.
- First aid or medical guidance.
- Downloading YouTube videos or transcripts; Spanner only links to tutorials.
- Buying parts inside the app (Spanner links to the retailer; no checkout).
- Sponsors we're not competing for: **MongoDB** (Databricks is our data platform), **Synthesia**, **Anam**, the AI for Science, Poker and Reply tracks.

## Constraints
- **Time:** build from Sat 14 Nov ~13:00 to **feature freeze Sun 15 Nov 10:00** (about 20 hours), then demo. Checkpoints and merges at 14:00, 18:00, 22:00, 03:00. Sleep in pairs 03:00–07:00.
- **Demo object:** not chosen yet (the 15:00 deadline has passed; choose it now). It must be easy to bring to the Huxley Building, safe, with a fault visible from outside and a fix short enough to do live, **and the fault must need a replacement part** with a readable model label, so the Linkup agent has something real to find.
- **Team:** 4 people, all on the same stack and locked dependencies. Each person edits only their own folders. `backend/contracts.py` changes only through P3, announced in chat, at checkpoints.
- **Machines:** Windows laptops; iPhones with Expo Go.
- **Secrets:** API keys only in `.env` (never committed): Anthropic, ElevenLabs, Databricks, fal, Together, Linkup, Tavily.
- **Demo safety:** everything must still work with the venue Wi-Fi down (cached research, cached media, hotspot, backup video).
- **Claude API calls:** use the `/claude-api` skill before writing any Claude call; prompts live in `backend/brain/prompts/*.md`.

## Build steps
The full technical plan (contracts, API routes, repo layout, per-role tasks, latency budget, risks) is in the approved plan; it should be copied into the repo as `PLAN.md` so every teammate's Claude Code can read it.

1. **Hour 0 (P3, blocks everyone):** repo skeleton, `pyproject.toml` + `uv.lock`, `.env.example`, `backend/contracts.py` v1, fixtures (`fixtures/frames`, `fixtures/audio`, `fixtures/outputs`), mocks (`USE_MOCKS=1`), `CLAUDE.md` team rules, `CONTRACTS.md`. Create `main` from `master`; branches `feat/app`, `feat/eyes`, `feat/voice`, `feat/brain`.
2. **13:00–14:00, in parallel:** P1 designs Figma foundations, key components and the Session screen, and checks that a GLB renders in Expo Go and that the camera can take a frame per second. P2 tests YOLO-World accuracy on the fixture photos and picks the 3 fal models. P3 publishes contracts and mocks and visits the Adaption booth. P4 sets up Databricks and tests one write. **Contracts frozen at 14:00.**
3. **14:00–18:00:** each module works on its own against mocks and fixtures. P1: camera + HUD + step card from the Figma designs; the rest of the screens and the prototype in Figma. P2: preprocess, detect, understand. P3: speech-to-text, TTS, Pass 1, fast loop, tunnel. P4: research agent, Pass 2, store writes. **Checkpoint:** `scripts/run_pipeline.py` runs every stage on fixtures.
4. **18:00–22:00:** integration. The app talks to the real backend; slow loop and events queue; `/debug` page; box smoothing; fal illustrations and 3D scan; state machine, alerts, safety; Adaption dataset generation starts. **Checkpoint:** point the phone at a photo, see boxes, hear the diagnosis, see illustrated steps.
5. **22:00–03:00:** the 3D panel and its commands, scan viewer, clips, alert banner; the repair pack for the chosen object; pre-made step clips; research cached for the demo fault; latency tuning; **Adaption fine-tune launched ~01:00**. **Checkpoint:** the full demo flow works once on the real object.
6. **03:00–07:00:** sleep in pairs; bug fixes from the 03:00 run; the fine-tune trains overnight.
7. **Sun 07:00–10:00:** visual pass against Figma, summary screen, edge cases (blur, darkness), Adaption evaluation and the decision to switch it on, Databricks dashboard, pitch numbers. **10:00 feature freeze**, record the backup video, rehearse 3 times.

**How we know it's done:** a full repair on the real object on the iPhone, with diagnosis, spoken plan, 3D, illustrations, a deliberate mistake that triggers an alert, a voice question answered in under 5 s, step progression and the summary screen; the session's rows in every Databricks table; the Adaption comparison table printed; a full rehearsal with the Wi-Fi off.

## Open questions
- **Demo object and fault**: must need a replacement part (overdue).
- **Are bounties open only to teams in that main track?** Ask the organisers (we chose Autonomous Agents).
- **Official judging criteria** for the main track.
- **Expo vs Swift:** does anyone have a Mac and a strong preference for native? (Default: Expo.)
- **Adaption:** credits, API or web platform only, which base models can be fine-tuned (ask at their booth).
- **fal:** exact model IDs for text-to-image, image-to-3D and image-to-video (P2 picks at kickoff).
- **Figma:** Dev Mode / MCP access for the weekend (ask at their booth), and who on the team designs.
- **Team roles:** which person takes P1, P2, P3 and P4.
