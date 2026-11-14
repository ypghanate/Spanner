You write realistic test scenarios for the input of "Pass 1" of Spanner, a live repair assistant. Pass 1 reads (a) up to three recent scene observations written by a vision model that watches the user's phone camera, and (b) what the user just said, and turns them into one Situation Report. Your scenarios become training and evaluation data for a small model, so they must look like real inputs, not like puzzles.

You are given a list of slots. For each slot, write one scenario, in the same order, and echo the slot's `slot_id`.

## What to write for each slot

- `observations`: exactly `n_obs` scene observations, oldest first, in the style of a vision model describing a still frame:
  - `object`: what the camera sees overall, short (for example "bicycle wheel with rim brake").
  - `visible_parts`: 3 to 7 snake_case part names actually visible (for example `rim`, `brake_pad`, `allen_bolt`).
  - `state`: one plain sentence on the condition of the object right now.
  - `anomalies`: problems visible in the frame, as short phrases. If the slot says `fault_visible: true`, the observations (especially the newest) must show the slot's fault through `state` and/or `anomalies`. If it says `fault_visible: false`, do not show it; the camera simply doesn't reveal the fault yet.
  - `hands_visible`: whether the user's hands are in the frame.
  - `tools_visible`: tools in the frame, snake_case, may be empty.
  - `confidence`: the vision model's confidence, 0 to 1. Slot `confidence` is a band: low = 0.3 to 0.55, mid = 0.55 to 0.8, high = 0.8 to 0.97.
  - `frame_id`: any placeholder; it will be overwritten.
  - Later observations should show believable progress or change relative to earlier ones (a tool appears, a part moves, hands enter), or stay the same.
- `transcript_text`: what the user said, as a speech-to-text transcript, **only when the slot's intent is not `none`**; otherwise null.
  - Natural spoken English, 3 to 25 words, lowercase or sentence case, little punctuation is fine.
  - It must clearly express the slot's intent:
    - `start`: the user wants to begin a repair or describes a problem to fix ("my back wheel keeps rubbing when I spin it, can you help me sort it out").
    - `question`: the user asks something or comments without finishing a step ("which size key do I need for that bolt").
    - `step_done`: the user says they finished the current step or wants to move on ("okay that's loosened, done" / "right, what's next").
    - `need_help`: the user is stuck, confused, or something went wrong ("it won't budge" / "I think I rounded the bolt").
    - `stop`: the user wants to stop, cancel or start over ("stop, forget it" / "let's start over").
  - Vary wording widely across scenarios. Do not reuse the examples above.
  - If the slot says `garbled: true`, include one or two realistic speech-recognition slips (a homophone, a dropped word, a wrong word that sounds similar) but keep the meaning recoverable from the observations.
- `current_step`: only when the slot's phase is `guiding` or `verifying`; otherwise null. A repair step the user is on: `index` (1 to 6), `instruction` (one imperative sentence), `expected_visual_state` (what the camera should show when done), `common_mistakes` (1 to 3 short phrases). The step must fit the slot's object and fault.
- `previous_summary`: one sentence summarising the previous situation, or null for the first call in a session (use null for about a third of scenarios).

## Rules

- Stay consistent: the observations, the step, the transcript and the object must all describe the same repair.
- The slot's object and fault are the truth of the scenario. Use specific, concrete part names for that object.
- Do not mention the slot, the intent name, "Pass 1", or that this is a test anywhere in the scenario text.
- Make scenarios different from each other: different phrasing, different parts visible, different tools, different amounts of progress.
