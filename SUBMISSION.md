# Spanner: submission texts

One section per prize. Each names the exact moment in the demo and the file where the sponsor does its job.
Square brackets mark numbers to fill in after tonight's real-keyboard runs.

Repo: https://github.com/ypghanate/Spanner
Dashboard: https://dbc-e2205585-ca78.cloud.databricks.com/dashboardsv3/01f1e0626c6410fcabd7e34a5f6d10d3/published
Genie: https://dbc-e2205585-ca78.cloud.databricks.com/genie/rooms/01f1e062be211dd88b29266342579fb7

---

## One-liner (use everywhere)

Spanner is a repair coach that watches you through the laptop camera, shows the exact step you are on as a
3D hologram, talks you through it, warns you before the mistakes other people made, and finds a verified
replacement part the moment you need one.

## What a judge sees in two minutes

1. "My F key is dead." Tap once; from then on it is voice only.
2. Unplug the cable: the hologram follows you to step 1 by itself. Pull the keycap, then the switch: it follows.
3. The switch is out. Spanner says, unprompted: "You'll need a new switch. Cheapest that fits your RK61 is
   £8.52 on AliExpress, verified on three sites. Repairing saves £51 and half a kilo of e-waste."
4. At step 7 Spanner says: "Heads up, people often put the switch in rotated here." That warning came from
   Databricks: two earlier sessions made that mistake.
5. Put the new switch in rotated on purpose: red flash, spoken correction. Fix it, test the key, done.
6. Second screen: the Databricks dashboard shows where people get stuck, the warning it learned, the money
   and e-waste saved, and the measured latency of every live decision. A judge asks Genie a question.

---

## Main track: Autonomous Agents

Spanner is an agent that looks, decides, acts and checks, step after step, without being driven.

- **Looks.** Every 1.5 s the laptop webcam frame goes to Claude with the full 11-step procedure in context
  (`backend/coach.py`). It answers: which step the user is on, whether it is done, and whether a known
  mistake is happening.
- **Decides.** The 3D hologram (`training/viewer/`) jumps to the detected step when two frames agree, advances
  on its own when the camera sees the step finished, and never waits for a click.
- **Acts.** It speaks (ElevenLabs), animates the exact motion of the step on a 255-part model of the RK61,
  flashes and corrects on a mistake, and when it sees the old switch come out it starts the replacement-part
  research itself (`backend/shop.py`, `maybeAutoShop` in `viewer.js`).
- **Learns.** Every mistake it catches is written to Databricks; mistakes seen in two or more sessions
  become a spoken warning for the next person at that step (`training/feedback.py`).

No buttons, no tabs, no mocks: `/health` reports all ten pipeline stages real; 280 tests pass. Measured
latency of one camera decision: about 2.5 s (Claude Haiku, vision, no extended thinking).

---

## Databricks bounty

Databricks is where Spanner learns, and it sits inside the live loop, not beside it.

- **Unity Catalog** `workspace.training`: bronze tables for sources, web pages, video segment labels,
  procedures, parts cards, offers, live mistakes and per-stage latency; a Volume for raw files
  (`databricks/sql/setup.sql`, `training/databricks_sync.py`).
- **Lakeflow pipeline with expectations** (`databricks/pipelines/training_quality.sql`): quality checks on
  Claude's video labels, silver/gold procedures, `gold_part_prices` (drops unpriced or unlinked offers),
  `gold_live_mistakes`, `gold_stage_latency`, `gold_eval_scoreboard`.
- **Vector Search** index `kb_steps_index` over the procedure steps; the brain queries it live.
- **The learning loop.** The coach writes every caught mistake to `bronze_live_mistakes` as it happens.
  `learned_mistakes()` asks Databricks which mistakes two or more distinct sessions made; the viewer speaks
  that warning when the step opens. No refresh, no manual step: the next user is warned by the last ones.
- **Measured, not estimated.** Every live stage, including the camera coach, writes its latency to
  `bronze_stage_timings` in 5-second batches.
- **AI/BI dashboard + Genie** (`databricks/dashboards/spanner_training.lvdash.json`,
  `databricks/deploy_dashboard.py`): where people get stuck, what the next person is warned about,
  money and e-waste saved by repairing (£339 across the researched devices), verified parts with evidence,
  latency per stage, knowledge-base sources, and the Adaption-vs-Claude evaluation. Genie answers questions
  over the same tables ("which step do people get wrong most?").
- **MLflow** experiment `/Shared/spanner-training` logs every sync and evaluation; the whole thing deploys
  from one Asset Bundle (`databricks/databricks.yml`).

Impact: a repair assistant that gets better with use, and a running tally of devices kept out of the bin.

---

## Best use of Linkup

Linkup is the live-research arm: it researches, verifies, acts, and cites, on fresh web data, while you repair.

- **Researches.** When the camera sees the old switch come out, Spanner starts a Linkup search on its own
  (`backend/parts_agent.py` → `find_offers`: three query shapes, structured output, union of results). Offers,
  the official manual, tutorials, and the live price of a new RK61.
- **Verifies.** Each offer's fit for the exact model is checked with a Linkup sourced answer and must be
  supported by two or more independent sites before it is shown as "fits your RK61". A safety-recall check
  is limited to the last two years with `from_date`.
- **Acts.** The agent makes the repair-or-replace decision: cheapest verified part vs a new unit, converted at
  today's ECB rate, with money and e-waste saved. "Repair it: £8.52 in parts vs £59.99 new. Saves £51.47 and
  0.5 kg of e-waste." If you ask for other keyboards instead, Linkup deep search returns real current models
  with official-store prices and pros and cons, and Spanner still shows what fixing yours would cost.
- **Cites.** Every offer links to its product page and to the evidence pages; every panel lists the pages
  Linkup read ("Linkup read 54 pages"). Citation coverage is measured and logged to Databricks.

Voice-driven and live: "show me replacement keycaps", "what other keyboards could I buy?" (`backend/shop.py`,
`/coach/shop`, `/coach/shop/verified`). Nothing is mocked; results are cached only after a live run.

---

## Best use of Tavily

Tavily has a separate job from Linkup: it builds Spanner's knowledge offline and fills in the gaps online.

- **Knowledge base.** `training/web_scrape.py` uses Tavily search + extract to find and read repair guides for
  the RK61 switch replacement (YouTube excluded by domain). Claude turns them, together with the video labels,
  into the canonical 11-step procedure that the coach follows and Databricks stores.
- **Live gaps.** In the shopping panel (`backend/shop.py`), Tavily finds a product photo for every offer and,
  when the retailer page hides its price from crawlers, Tavily search snippets plus Claude recover a current
  price for that exact product.

Two web tools, two roles: Tavily for building knowledge, Linkup for acting on it live.

---

## Best use of Adaption (draft: fill in after the run)

The most frequent decision Spanner makes is the step judge: from what the camera saw and the current step,
which step is the user on, is it done, is a known mistake happening. We fine-tuned that judge with Adaption.

- **Data.** Real: Claude-labelled segments of three RK61 repair videos plus [N] frames from tonight's live
  sessions, curated in Databricks; expanded and cleaned with Adaptive Data (quality score before/after:
  [x → y]). Real frames are held out for evaluation and never trained on.
- **Model.** [Qwen3.5-2B, LoRA] trained with AutoScientist ([k] iterations, best win rate [w]).
- **In the product.** The judge runs on the laptop after Claude describes the frame: no API call for the
  decision, works offline, [t] ms per decision, with Claude as automatic fallback (`training/step_tracker.py`).
- **Evaluation against Claude Haiku** on held-out real frames (n = [n]): step accuracy [a vs b], mistakes
  caught [c vs d], latency [t vs u]. Logged to MLflow and shown on the Databricks dashboard.
- **Flywheel.** Every live mistake lands in Databricks; the next Adaption run trains on it.

If the run is not back by submission: "Dataset, evaluation harness and a training run in flight; results
to follow", and the dashboard shows the empty scoreboard honestly.

---

## 60-second second-screen pitch (say this while the dashboard is up)

"Everything you just watched wrote itself here. This chart is where people get stuck: one bar per step,
counted in sessions. This row is the warning Spanner gave me at step 7; it learned that from the two runs we
did tonight. This is what the Linkup agent decided: repair, £8.52 against £59.99, half a kilo of e-waste not
thrown away, with the pages it verified on. This is how long each live decision takes, measured, not
estimated. And this is Genie: ask it anything."
