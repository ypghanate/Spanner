"""Real-Claude A/B test: does showing Claude the camera + YOLO + speech beat the old text-only way?

    uv run python scripts/eval_perception.py                      # uses the bicycle mockups in app/assets/img
    uv run python scripts/eval_perception.py --frame-a a.jpg --frame-b b.jpg --question "which key is this?"

Needs ANTHROPIC_API_KEY (in .env or the environment). Makes about 19 calls to Haiku 5.5 (a few cents):
1 vision call to describe the OLD frame, then N repeats of Pass 1 per question per way, and N of Pass 2.

Scene: frame A is an OLD keyframe, frame B is the live frame (what the camera sees as the user speaks).
  old way = Pass 1 / Pass 2 get only the stale text description of frame A;
  new way = they also get both frames, a close-up when one applies, and the YOLO tracks.
With the default images, B has a hand pointing at the chainring and A has no hand, so the right answer
to "what am I pointing at?" / "what changed?" is only knowable from the pictures. Keyword scoring is crude:
read the printed samples too. Pass your own pair (e.g. RK61 photos with a finger on a key) with --frame-a/-b.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402  (also loads .env)

HAND = ("hand", "finger", "pointing", "points", "touch")
PART = ("chainring", "chain ring", "chainwheel", "crank", "sprocket", "chain wheel", "gear")


def has(text: str, words) -> bool:
    """True if any word appears (substring, case-insensitive). An entry starting with "re:" is a regex."""
    import re
    return any(re.search(w[3:], text, re.I) if w.startswith("re:") else w.lower() in text.lower() for w in words)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frame-a", type=Path, default=ROOT / "app/assets/img/live-camera.png", help="the OLD frame")
    ap.add_argument("--frame-b", type=Path, default=ROOT / "app/assets/img/live-camera-hand.png", help="the LIVE frame")
    ap.add_argument("--question", default="What is this part I'm pointing at?")
    ap.add_argument("--change-question", default="What just changed in front of the camera?")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--model", default=None, help="Pass 1 model for this test (default: the configured Pass 1 model)")
    ap.add_argument("--effort", default="low", choices=["low", "medium", "high"], help="Pass 1 effort for this test")
    ap.add_argument("--demote-old-text", action="store_true",
                    help="experiment: when pictures are attached, label the older text descriptions as possibly wrong "
                         "and subordinate to the images (patched in here only; nothing in backend/ changes)")
    ap.add_argument("--long-edge", type=int, default=None,
                    help="resize both frames so the long side is this many px (the app sends 768; try 1024)")
    ap.add_argument("--step", type=int, default=1, help="0-based step of the procedure used for the Pass 2 answer")
    ap.add_argument("--hand-words", nargs="*", default=list(HAND), help="words that show the new thing was noticed")
    ap.add_argument("--part-words", nargs="*", default=list(PART), help="words that show the right part was named")
    a = ap.parse_args()
    if not get_settings().anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set (empty in .env, not in the environment). Nothing was called.", file=sys.stderr)
        return 2

    from backend.brain import pass1, pass2
    from backend.contracts import RepairPlan, SituationReport, Transcript
    from backend.mocks import fixture_json
    from backend.perception import build
    from backend.session import FrameSnap, Session
    from backend.vision import detect as vdetect
    from backend.vision.preprocess import preprocess
    from backend.vision.understand import _observe

    s_cfg = get_settings()
    if a.model:
        import dataclasses
        s_cfg = dataclasses.replace(s_cfg, pass1_model=a.model)
    n = a.repeats
    if a.demote_old_text:
        orig_render = pass1.render_user

        def render_demoted(inp):
            text = orig_render(inp)
            if inp.image_notes:
                text = text.replace(
                    "OBSERVATIONS (oldest first):",
                    "OLDER TEXT DESCRIPTIONS (machine-written from earlier frames; they can be outdated or WRONG. "
                    "The attached images are newer and override them wherever they differ):")
            return text
        pass1.render_user = render_demoted
        print("EXPERIMENT: older text descriptions are demoted when pictures are attached")
    questions = {"deictic": a.question, "change": a.change_question}
    print(f"model: {s_cfg.pass1_model} | effort: {a.effort} | repeats: {n}")

    a_jpeg, qa = await preprocess(a.frame_a.read_bytes(), "eval")
    b_jpeg, qb = await preprocess(a.frame_b.read_bytes(), "eval")
    if a.long_edge:                                   # same pipeline, different resolution
        import cv2
        import numpy as np
        from backend.frames import encode_jpeg
        redo = lambda path: encode_jpeg(cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR),  # noqa: E731
                                        long_edge=a.long_edge)
        a_jpeg, b_jpeg = redo(a.frame_a), redo(a.frame_b)
        print(f"images resized to a long side of {a.long_edge} px ({len(a_jpeg) // 1024} KB and {len(b_jpeg) // 1024} KB)")
    print(f"frame A sharpness {qa.sharpness} | frame B sharpness {qb.sharpness}")
    vdetect.reset()
    det_a, det_b = vdetect.detect(a_jpeg, "eval"), vdetect.detect(b_jpeg, "eval")
    show = lambda ds: [(d.label, round(d.score, 2), round(d.box.w * d.box.h, 2)) for d in ds]    # noqa: E731
    print("YOLO on A:", show(det_a), "| on B:", show(det_b))

    obs_a = _observe(a_jpeg, "eval-A", det_a, None)                      # the stale text description of the old frame
    print(f"old text observation: {obs_a.state!r} | hands_visible={obs_a.hands_visible}")

    now = time.time()
    s = Session(id="eval", phase="diagnosing")
    s.recent_frames.extend([FrameSnap("A", now - 4.0, a_jpeg, det_a), FrameSnap("B", now - 0.4, b_jpeg, det_b)])
    s.observations.append(obs_a)
    s.last_observation_at = now - 4.5

    async def call(inp, images):
        t0 = time.perf_counter()
        msg = await pass1._get_client().messages.parse(
            model=s_cfg.pass1_model, max_tokens=1024, system=pass1.load_system_prompt(),
            messages=[{"role": "user", "content": pass1._user_content(inp, images)}],
            output_format=SituationReport,
            # Sonnet 5.5 rejects thinking disabled; "between_tools" is its way of turning thinking off
            thinking={"type": "between_tools"} if s_cfg.pass1_model.startswith("claude-sonnet-5-5") else {"type": "disabled"},
            output_config={"effort": a.effort},
            timeout=s_cfg.claude_timeout_s * 2)
        return msg.parsed_output, time.perf_counter() - t0, msg.usage.input_tokens

    results = {}
    view = None
    for qname, question in questions.items():
        tr = Transcript(text=question, language="en", duration_s=2.0)
        view = build(s, speech_end_ts=now + 0.7, duration_s=2.0, now=now)
        for label, inp, imgs in (("old (text only)", pass1.build_input(s, tr), None),
                                 ("new (camera + YOLO)", pass1.build_input(s, tr, view), view.images if view else None)):
            results[(qname, label)] = [await call(inp, imgs) for _ in range(n)]
    print("perception shown on the new way:", view.summary() if view else None)

    print("\n" + "=" * 100)
    for (qname, label), runs in results.items():
        texts = [f"{r.summary} {r.camera_sees} {r.change_since_last}" for r, _, _ in runs]
        hand, part = (sum(has(t, a.hand_words) for t in texts), sum(has(t, a.part_words) for t in texts))
        both = sum(has(t, a.hand_words) and has(t, a.part_words) for t in texts)
        lat, tok = [x for _, x, _ in runs], [x for _, _, x in runs]
        print(f"[{qname:7}] {label:20} noticed the new thing {hand}/{n} | named the part {part}/{n} | both {both}/{n} | "
              f"latency median {statistics.median(lat):.2f}s (min {min(lat):.2f}, max {max(lat):.2f}) | input tokens {int(statistics.median(tok))}")
    print("\nevery run's one-line summary:")
    for (qname, label), runs in results.items():
        for i, (r, _, _) in enumerate(runs, 1):
            print(f"  [{qname[:6]}|{label[:3]}|{i}] {r.summary[:230]}")
    for qname in questions:
        for label in ("old (text only)", "new (camera + YOLO)"):
            r = results[(qname, label)][0][0]
            print(f"\n--- sample [{qname}] {label}\n  camera_sees: {r.camera_sees}\n  change_since_last: {r.change_since_last}\n  summary: {r.summary}")

    print("\n" + "=" * 100 + "\nPass 2 _answer (the spoken reply), fed each way's own Pass 1 report\n")
    try:
        from backend.knowledge import plan_from_knowledge
        plan = plan_from_knowledge() or RepairPlan.model_validate(fixture_json("repair_plan"))
    except Exception:
        plan = RepairPlan.model_validate(fixture_json("repair_plan"))
    step = min(a.step, len(plan.steps) - 1)
    print(f"procedure: {plan.object} | using step {step + 1}/{len(plan.steps)}: {plan.steps[step].instruction[:90]}")
    view = build(s, speech_end_ts=now + 0.7, duration_s=2.0, now=now)
    for label, perc in (("old (text only)", None), ("new (camera + YOLO)", view)):
        # the real chain: each way's Pass 2 receives that way's OWN Pass 1 report (so the old way's report
        # is built from the stale text, and the new way's from what Pass 1 saw in the pictures)
        report = results[("deictic", label)][0][0].model_copy(
            update={"user_intent": "question", "user_utterance": questions["deictic"]})
        outs, lats = [], []
        for _ in range(n):
            t0 = time.perf_counter()
            outs.append(await asyncio.to_thread(pass2._answer, plan, step, report, perc))
            lats.append(time.perf_counter() - t0)
        hits = sum(has(o, a.hand_words) or has(o, a.part_words) for o in outs)
        print(f"[pass2 ] {label:20} noticed the hand or named the part {hits}/{n} | latency median {statistics.median(lats):.2f}s")
        print(f"   sample: {outs[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
