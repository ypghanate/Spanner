# Spanner: the pitch

Sections 1 to 4 and the close are the spoken pitch (about 2 minutes plus the demo). Sections 5 to 10 are the
sponsor deep-dives: each has a 30-second spoken version in bold, then the detail for the judges' questions,
the written submission, and the sponsor booths. Everything in the detail is in the repo and can be shown.

---

## 1. Hook

Hold up the keyboard.

"This keyboard has one dead key. Fixing it costs eight pounds and ten minutes. Most people bin it and spend
sixty. Not because the repair is hard. Because nobody is standing next to them saying: *that one, pull it
straight up, no, the other way.*

Spanner is that person. It watches you through the camera, shows you the exact step as a hologram, talks you
through it, stops you before you make the mistake everyone makes, and finds the part. Hands-free. Let me show
you."

(Demo: 2 minutes. Then the second screen.)

## 2. The problem

"Repair guides are written for people who already know the names of the parts. A video shows someone else's
hands at someone else's speed. You can't ask either of them *am I doing this right?*

So three things go wrong. People don't start, because they don't know if they can. People get stuck halfway,
with the thing in pieces. And people make the same small mistakes everyone makes at that step, because
nothing warned them.

The result is a drawer full of things that were ten minutes from working."

## 3. The solution

"Spanner is a repair coach with eyes.

You put the broken thing in front of the laptop and talk. Spanner knows the procedure, because it built it
from real repair videos and guides. It looks at every frame and works out which step you are on. The 3D model
on screen plays that exact step, on your exact keyboard, with the tool you need to hold. When you finish a
step, it moves on. When you do something wrong, it says so before the damage is done. When you pull out the
broken part, it has already found the replacement, checked that it fits, and told you what you are saving.

And every session makes the next one better. The mistakes you make tonight become the warnings someone else
hears tomorrow."

## 4. The decisions we made

"Four decisions shaped this.

**One real object, done properly.** We picked the Royal Kludge RK61, a hot-swap keyboard, and built a
255-part model of it in code: every keycap, switch, socket, the PCB, the case, the tools. The procedure, the
animations and the camera understanding are all about this keyboard. Depth over breadth.

**Voice only.** No buttons, no tabs, no menus. If you need a hand to tap, you are not repairing. Panels
appear when you ask and disappear when you say close.

**Learn from the real world, never download it.** The procedure comes from three repair videos and two
written guides. The videos were watched, not downloaded. Our own sessions are the training data.

**No mocks.** Every stage you see runs for real: the vision, the voice, the web research, the warehouse. We
can show you the health endpoint. Ten stages, ten real."

---

## 5. Why Databricks

**Spoken (30 s):** "Databricks is not our database. It is the reason Spanner gets better every time someone
uses it. Every session writes what the camera saw and every mistake it caught into Unity Catalog. A Lakeflow
pipeline with quality checks turns Claude's labels of real repair videos into the procedure. And the loop
closes live: before every step, Spanner asks Databricks what people got wrong here, and says it out loud. The
warning you heard at step seven came from the two sessions we ran last night. The dashboard shows where
people get stuck, what it learned, and what repairing saved. Genie sits on the same tables: ask it anything."

**Why Databricks and not a file, a Postgres table or a vector database.** We needed five things at once:
a place to land raw, messy, multi-source data (video labels, web pages, prices, live mistakes); a way to
enforce quality on AI-generated labels before they become the procedure a user follows; semantic lookup over
the steps for the agent; dashboards and a natural-language interface for people who are not us; and all of
it deployable as one unit. Databricks is the only sponsor that does all five, and the learning loop is a
*data* problem, not a model problem: the thing that improves Spanner is rows accumulating, being checked, and
flowing back. That is a lakehouse job.

**Exactly what we used, and what each piece does for the product**

- **Unity Catalog, schema `workspace.training`, plus a Volume `raw`.** Eleven bronze tables and the raw
  files behind them. The ones that matter on stage: `bronze_live_mistakes` (every mistake the camera caught:
  session, step, what, when), `bronze_stage_timings` (the measured latency of every live stage, including the
  camera coach), `bronze_part_cards` and `bronze_part_offers` (every Linkup decision and offer, with the
  verification evidence), `bronze_segment_labels` (Claude's labels of the repair videos, 111 windows).
  Everything the agent knows or decides has a row. Nothing is in a local file only.
- **Lakeflow pipeline with expectations** (`databricks/pipelines/training_quality.sql`). This is where we
  stop the AI from teaching itself nonsense. Claude labels the repair videos in 10-second windows; the
  pipeline keeps only labels with the fields filled and a confidence above threshold (`gold_label_quality`
  reports how many windows per video survived), drops offers without a price or a link
  (`gold_part_prices`: `EXPECT (price > 0)`, `EXPECT (url LIKE 'http%') ON VIOLATION DROP ROW`), and merges
  learned mistakes into the canonical steps (`gold_procedure_steps.common_mistakes`, with
  `learned_mistake_count` so you can see which warnings came from users rather than from the videos).
  Medallion for real: bronze is what the AI said, silver is what passed, gold is what the product uses.
- **Vector Search**, index `kb_steps_index` on endpoint `spanner-vs`, over `kb_steps`. The brain's
  `search_steps()` queries it live, so a spoken question like "which way does the switch go?" is answered
  with the matching step's text, not a guess.
- **The learning loop, live.** `training/feedback.py`: the coach writes each caught mistake to
  `bronze_live_mistakes` the moment it happens; `learned_mistakes()` asks Databricks for mistakes seen in two
  or more *distinct sessions* (one person repeating an error doesn't count), cached for 30 seconds; the viewer
  speaks the warning when that step opens. No nightly job in between. The next user is warned by the last
  ones. We chose the two-session threshold deliberately: one session is noise, two is a pattern.
- **Measured latency, not estimated.** Every stage of both pipelines (phone and laptop) calls
  `record_stage_timing`, batched to Databricks every 5 seconds. The dashboard's "how long each live decision
  takes" is p50 and p90 of real calls. We can tell a judge, with data, that one camera decision takes about
  2.5 seconds.
- **AI/BI dashboard** (`databricks/dashboards/spanner_training.lvdash.json`, published with
  `databricks/deploy_dashboard.py`). Designed as the second half of the pitch, in reading order: where people
  get stuck (one bar per step, coloured by whether it has become a warning), what the next person is warned
  about and why, repair-or-replace impact (£339 saved across the researched devices so far, kilograms of
  e-waste avoided), verified parts with their evidence, latency per stage, what the knowledge base was built
  from, and the Adaption-versus-Claude evaluation on separate axes for accuracy and time.
- **Genie space** on the same tables with written instructions about what each table means, so a judge can
  ask "which step do people get wrong most?" or "how much has repairing saved?" and get the SQL and the
  answer. We tested it through the API: 21 seconds, correct SQL, correct number.
- **MLflow** experiment `/Shared/spanner-training`: every sync, dataset build and evaluation is a run with
  parameters and artefacts, so the Adaption comparison is reproducible, not a screenshot.
- **Asset Bundle** (`databricks/databricks.yml`): pipeline, index builder and dashboard in one deployable
  definition.

**What we rejected.** Writing live sessions to SQLite and syncing later: then the warning could not be live.
A separate vector database: one more system, and the steps already live in Databricks. Training on the raw
labels: the expectations exist because Claude's labels are good but not clean, and a user following a wrong
step is worse than no step.

**The creativity, depth and impact answer in one line each.** Creativity: the product learns from its users
through the lakehouse, and says what it learned out loud. Depth: medallion with expectations on AI labels,
Vector Search in the live loop, batched telemetry, dashboard, Genie, MLflow, one bundle. Impact: a running,
auditable tally of devices kept out of the bin and the money saved, per decision, with its sources.

## 6. Why Adaption

**Spoken (30 s):** "The decision Spanner makes most often is small: given what the camera sees and the step
you're on, is it done, and is a known mistake happening? A frontier model is the wrong tool for that: slow,
paid per call, needs the internet. So we fine-tuned that judge with Adaption on real repair data, and it runs
on this laptop. No API call for the decision, works with the Wi-Fi off, Claude as the fallback. On the
dashboard you can see it against Claude Haiku on real frames. And every mistake caught tonight goes into the
next run: the more people repair, the better and cheaper the judge gets."

**Why fine-tune at all, and why this decision.** Spanner makes one decision every couple of seconds for the
whole repair: *status of the current step*. It has a fixed input (step text, what done looks like, the
known mistakes, a description of the frame) and a tiny output (`{"status": ..., "mistake": ...}`). That is
the textbook case for a small specialised model: narrow task, repetitive, latency-sensitive, and the
procedure can live in the weights instead of being re-sent as a 1,500-token prompt on every call. We
deliberately did *not* fine-tune Pass 1 or the planner; those need world knowledge. We fine-tuned the one
thing that needs *our* knowledge.

**Why Adaption rather than doing it by hand.** Two reasons. First, data: we have real but small data
(segments of three repair videos, labelled by Claude, plus our own live frames). Adaptive Data is built for
exactly that: deduplicate, rephrase prompts, expand the domain rows, and report a quality score before and
after, so we can say the dataset got better, with a number. Second, the training loop: AutoScientist picks the
recipe, trains, evaluates against the base model and iterates, which is what a four-person team at a hackathon
cannot do properly in one night. We spent the night on the data, not on hyperparameters.

**The data story, honestly.** `training/adaption_dataset.py` derives examples with no hand-labelling: for
every 10-second window Claude labelled in a video, and for the step it falls in plus its neighbours, the status
follows from the procedure's time ranges (before the step: not started; inside: in progress; after: done;
inside with a warning: error, with the mistake named). Split is by video, never by frame, so evaluation is
honest. Tonight's build: 610 rows, RK61 only, 60 of them errors, synthetic rows clearly marked. Live session
frames are held out for evaluation and never trained on. Every row carries its source, so a judge can ask
"how much of this is real?" and get an answer.

**In the product.** `training/step_tracker.py`: the same system prompt and input format in training,
evaluation and live; a strict 0.8-second budget; on timeout or bad JSON, Claude Haiku answers instead and the
fallback is counted. The camera panel shows which judge answered and how long it took.

**The evaluation we show.** On held-out real frames: step accuracy, mistake recall, latency p50, cost per
thousand decisions, works-offline yes/no, for the Adaption model and for Claude Haiku, with the sample size.
Logged to MLflow, shown on the Databricks dashboard as `gold_eval_scoreboard`. [Fill in: n, accuracy,
recall, latency, Adaptive Data quality before/after, base model, iterations, win rate.]

**What we rejected.** Fine-tuning the vision model itself: the right long-term move, but the real image data
does not exist yet at the scale it needs, and it cannot run on a laptop without a GPU. Training on synthetic
data alone: easy, and meaningless. Claiming the fine-tune makes the whole loop faster: it doesn't; vision
still costs seconds. What it makes is the *decision* local, free and offline.

## 7. Why Linkup

**Spoken (30 s):** "The moment the old switch comes out, Spanner needs fresh facts from the live web: who
sells this part, does it fit this exact keyboard, is there a recall, what does a new one cost. That is Linkup.
It researches across the official store, AliExpress, eBay and specialist shops. It verifies: an offer is only
marked *fits your RK61* when two or more independent pages confirm it. It checks recalls in the last two
years. And it acts: repair or replace, eight pounds fifty against sixty, half a kilo of e-waste. Every claim
links to its page. And Spanner started the search itself when it saw the switch come out."

**Why a live search API and not Claude's own knowledge or a cached parts list.** Prices change weekly, stock
changes daily, and compatibility claims on retailer pages are wrong often enough that we would not put one in
front of a user unverified. A model's training data cannot answer "is this in stock today at this price",
and a cached list is stale the day after the hackathon. The brief asks for an agent that researches, verifies
and acts on fresh information with citations. That is the whole parts agent, not a feature of it.

**Exactly what we used, and why each mode.**

- **Structured search** (`output_type="structured"` with a Pydantic schema): for offers and for alternative
  keyboards, so the agent gets `name, retailer, price, currency, url, compatible, why` as data, not prose to
  parse. We run three query shapes per part and union the results, because deep search results vary run to
  run and one shape alone sometimes comes back empty.
- **Deep search** where coverage matters (new-unit price, alternative keyboards from review sites plus
  official store pages) and **standard search** where speed matters (the quick offers grid the user sees in
  about 30 seconds while the deep verification runs behind it).
- **Sourced answers** (`output_type="sourcedAnswer"`) for the compatibility question: "does part X fit the
  RK61?" comes back with the pages that support it. Claude then judges fit from those pages, and the offer is
  marked verified only with two or more *independent* supporting sites (same domain counts once). Three
  verified offers per part on tonight's cards, each with 2–5 sources.
- **`from_date`** on the recall check: a recall search only makes sense within a window, so we ask for the
  last two years and show "no safety recalls found in the last two years" rather than silence.
- **Fetch** (`render_js=True`, `mode="pro"` fallback): the official manual (we read the RK61 manual PDF to
  extract specs with page numbers), and product pages for prices that only render with JavaScript.
- **Sources on every panel.** Every search is called with `include_sources=True`; the shopping panel ends
  with "Linkup read 54 pages" and the links. Citation coverage is computed and logged to Databricks
  (`gold_eval_scoreboard`, `eval_name = parts_agent`).

**The "acts" part, which most entries miss.** The agent does not stop at a list. It computes repair cost
(cheapest verified offer, converted at today's ECB rate from frankfurter.dev) against the live price of a new
unit, decides repair or replace with a 60% rule, and states the saving in money and in e-waste. If you ask
for *other keyboards instead*, it still shows "fixing yours costs £8.52" above the alternatives. And it is
autonomous: `maybeAutoShop` in the viewer starts the search when the camera reports the old switch out.

**What we rejected.** Showing unverified offers as compatible (we show them, but marked). Amazon as the main
source (it blocks price reads; we prefer shops whose prices are on the page). A single search per part (too
flaky). Running verification before showing anything (users wait 2.5 minutes; instead the quick grid appears
first and the verified box slides in when ready).

## 8. Why Tavily

**Spoken (30 s):** "Tavily and Linkup have different jobs. Linkup acts live, in the session. Tavily built the
knowledge before the session and fills the gaps during it. Before any user showed up, Tavily searched and
extracted the written repair guides for this keyboard; Claude combined them with the video labels into the
eleven-step procedure. Live, Tavily finds the product photo for every part on screen and recovers a price
when a retailer hides it from crawlers."

**Why two web tools instead of one.** They are good at different things and we used each where it is best.
Building a knowledge base is a batch job over documents: you want search *plus* clean extraction of whole
pages, cheaply, many at a time. That is Tavily's search-and-extract. Acting live on a user's exact model is a
verification job: structured results, sourced answers, JavaScript-rendered pages, date windows. That is
Linkup. Using one for both would have meant either a worse knowledge base or a slower, less verifiable live
agent.

**Exactly what we used.**

- **Search with `exclude_domains`** (`youtube.com`, `youtu.be`): the written guides only, because videos take
  a different path (watched, never downloaded). Queries come from `training/repair.json`
  (`web_queries`), so the knowledge base is reproducible per repair.
- **Extract** on every guide page found, into `bronze_web_pages`; Claude turns each page into a candidate
  procedure; the Lakeflow pipeline reconciles web and video procedures into the canonical one. The dashboard
  shows how many steps came from web guides versus videos.
- **Image search** (`include_images=True`) in the shopping panel: every offer gets a real product photo, in
  parallel, so the panel is something a person can choose from, not a list of links.
- **Snippet search as a price fallback**: when a retailer page will not give up its price to a crawler, Tavily
  search snippets for that exact product, plus Claude choosing only a price that belongs to the same item and
  pack size, recover it. Items where that fails show "see price" with the link, never a guessed number.

**What we rejected.** Scraping with our own crawler (blocked, slow, fragile on venue Wi-Fi). Letting Tavily
return YouTube results (we would have been tempted to download them).

## 9. Why Anthropic

**Spoken (30 s):** "Claude is the brain in four places. The eyes: every camera frame goes to Claude with the
whole procedure in context, and it answers which step, done or not, mistake or not. The voice: ask *which way
does the switch go in* and Claude answers with the current step in mind, in two spoken sentences. The
teacher: Claude watched the repair videos and read the guides and wrote the procedure, with what *done* looks
like and the common mistakes at every step. And the judge of the web: Claude decides whether an offer really
fits this model and whether a page actually shows a price."

**Why Claude for these jobs.** Three capabilities we leaned on hard. Vision with long context: the coach sends
a frame *and* the full 11-step procedure with done-states and known mistakes, and asks for one structured
answer; that needs a model that reads an image against a long rubric reliably. Structured outputs: every
Claude call in Spanner returns a Pydantic model (`CoachView`, `SceneObservation`, `StepStatus`, the offer
schemas); no JSON parsing, no retries on malformed output. And judgement over web pages: deciding "do these
two pages really say this part fits this keyboard" is a reading-comprehension task, which is where a frontier
model earns its cost.

**Exactly what we used, where.**

- **The coach** (`backend/coach.py`): Claude Haiku 5.5 with vision, extended thinking off (one frame, one
  label: thinking only added latency), `output_format=CoachView`, effort low, the procedure in a cached system
  prompt. Measured: about 2.5 seconds per frame. We tested Sonnet and Haiku side by side: same step accuracy
  on our frames, Haiku steadier on confidence, so Haiku is the default.
- **Scene understanding** in the phone pipeline (`backend/vision/understand.py`): Haiku vision to
  `SceneObservation`, with the detector's boxes as hints.
- **Spoken questions** (`/coach/ask`): the procedure plus the current step as context, two short sentences,
  about 1.6 seconds.
- **The teacher**: Claude Opus labels the repair videos (via Gemini's view of public YouTube URLs, no
  downloads) and writes the procedures from the web guides; those labels are what the Lakeflow expectations
  check. Claude also writes the marked synthetic rows that balance the Adaption dataset.
- **The judge of the web** (`backend/parts_agent.py`, `backend/shop.py`): compatibility verdicts from Linkup's
  sourced pages, manual facts with page numbers, and price extraction from page text.
- **Prompt caching** on every system prompt that carries the procedure, so repeated calls pay for the frame,
  not the rubric. **Server-side fallback** on the models that support it, so a busy region never stalls a
  demo.

**What we rejected.** Thinking on the coach (slower, no gain on a one-label task). Sonnet everywhere (no
measured gain on the frames we tried). Free-text outputs anywhere (one malformed answer in a live loop is a
frozen hologram).

## 10. Why Figma

**Spoken (30 s):** "Everything on screen was designed in Figma first: the hologram HUD, the step card, the
camera panel, the shopping panel, the warning states, the dashboard page. Foundations as variables, components
with their states, and a clickable prototype of the whole demo, which doubled as our storyboard and is the
backup if the live demo fails."

**Why design first for a two-day build.** Spanner has one interface and it is voice-driven, so every pixel
that appears has to justify itself: nothing is clickable, so nothing can be explained by a hover or a
tooltip. The design file is where we decided what appears, when, and in which state, before any of it was
built. The states are the product: step in progress, step done, mistake, learned warning, searching, verified.
Getting those wrong in code is a rewrite; getting them wrong in Figma is ten minutes.

**What is in the file.**

- **Foundations as variables:** colour (`#05080F` navy, `#00E5FF` cyan, ok green, warn amber, danger red,
  glass panels), type (a monospace for readouts and tags, a clean sans for instructions), spacing scale,
  radius, glow and line widths. These are the same values as `training/viewer/index.html`, so design and code
  cannot drift.
- **Components with states:** the step card (text only, with tools, done, mistake, learned warning), the
  camera panel (watching, step detected, mistake), the shopping panel (searching, quick results, verified,
  other keyboards), part tags and callouts on the hologram, the wake screen.
- **Screens:** wake, repair in every phase, the shopping panels, the dashboard page (which also drove the
  reading order of the Databricks dashboard).
- **Flow:** a clickable prototype of the two-minute demo. It is the storyboard we rehearsed against and the
  backup if a camera or a venue network fails on stage.

[Status: the Figma file is being built from the shipped UI; the section above is the target. If it is not
complete by submission, say so and show what exists.]

---

## 11. Why fal

**Spoken (30 s):** "Spanner knows which key you pulled. So we asked fal to draw the next step on *your*
keyboard, not a stock one. This is a real RK61 photo; this is Nano Banana lifting the J keycap off it, every
other legend untouched, in twelve seconds; and this is Kling animating that still so you see the motion. One
photo in, the step on your own board out."

**What fal does in the design.** The camera frame that the coach already crops to the keyboard is the input.
A magenta ring marks the key the coach located; the edit model acts on that key and removes the ring. The
still arrives in about 12 s, the 5-second clip about two minutes later. Both are generated in the background
when a step starts and shown only when the user asks ("show me on mine"), so nothing covers the hologram
uninvited. Timings go to the same Databricks `stage_timings` table as every other live decision.

**What we measured** (all on a real RK61 photo, `fal/`):

| Job | Model | Time | Verdict |
|---|---|---|---|
| Edit the photo | Nano Banana edit | 12.6 s | chosen: right key, legends intact |
| Edit the photo | Nano Banana Pro edit | 17.7 s | same quality, slower |
| Edit the photo | FLUX Kontext Pro | 6.9 s | rejected: redrew the board, garbled legends |
| Animate the still | Kling 2.1 standard | 117 s | chosen: board still, hand moves |
| Animate the still | Hailuo 02 | 156 s | rejected: the board drifts |
| Photo to 3D | Trellis | one mesh, no parts | kept as the "any device" idea, not for repair steps |

**Where it stands.** Built end to end, run on a real frame, then taken out of the demo build the same night:
two minutes for the clip is too long for a two-minute demo, and the venue network dropped fal's queue domain
for long stretches. The outputs are in `fal/`, the wiring is in git history (`d0dc745`), and the honest line
on stage is "prototype, real outputs, not in the live loop".

---

## Close

"Spanner turns *I don't know if I can fix this* into *show me.* It watches, it guides, it warns, it shops,
and it learns from everyone who uses it.

Eight pounds, ten minutes, one keyboard out of the bin. Thank you."
