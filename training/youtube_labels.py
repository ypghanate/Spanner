"""Learn from YouTube repair tutorials WITHOUT downloading them.

Gemini's API accepts a public YouTube URL as video input, so Google's servers "watch" the video;
nothing is downloaded or stored except our own derived labels and the video's URL.

  1. discover  Linkup finds public tutorials for the configured repair (or pass --urls)
  2. label     Gemini watches each video by URL and returns 10 s window labels in OUR format
               (the same SegmentLabel the Claude video labeller produces)
  3. save      data/work/yt_<id>/segments.json  +  a sources.csv row (URL, licence note)
  4. procedure Claude merges the windows into that video's procedure (existing pipeline step)

After this, `training.adaption_dataset`, `pipeline merge` and `databricks sync` use these videos
like any other. Keep your own filmed videos as the held-out test set (`test_videos` in repair.json).

  python -m training.youtube_labels --discover 15
  python -m training.youtube_labels --urls https://www.youtube.com/watch?v=...  [--force]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from training import label, tracking  # noqa: E402
from training.schemas import SegmentLabel  # noqa: E402

WORK = ROOT / "data" / "work"
SOURCES = ROOT / "training" / "sources.csv"
# newest first; the newest are often overloaded (503), so fall back down the list
MODELS = [m for m in os.getenv("GEMINI_MODELS", "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash,"
                                                  "gemini-3.1-flash-lite").split(",") if m]


class Window(BaseModel):
    start_s: float
    end_s: float
    said: str
    label: SegmentLabel


class VideoLabels(BaseModel):
    is_repair_tutorial: bool
    duration_s: float
    windows: list[Window]


def video_id(url: str) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def discover(cfg: dict, n: int) -> list[str]:
    """Public tutorials for this repair, found with Linkup (links only)."""
    from backend.parts_agent import _linkup

    res = _linkup().search(
        query=(f"Find video tutorials that show step by step how to fix this: {cfg['fault']} on the "
               f"\"{cfg['object']}\". Run separate searches for: replacement or repair walkthroughs, "
               "DIY fixes, and official or manufacturer videos. Return the video titles and URLs."),
        depth="standard", output_type="searchResults", include_domains=["youtube.com"], max_results=n)
    urls = [r.url for r in res.results if video_id(r.url)]
    return list(dict.fromkeys(urls))[:n]


def _prompt(cfg: dict, seconds: int) -> str:
    parts = ", ".join(cfg.get("parts") or []) or "name parts plainly"
    tools = ", ".join(cfg.get("tools") or []) or "name tools plainly"
    return (
        f"This should be a repair tutorial: {cfg['object']}, fault: {cfg['fault']}.\n"
        f"Part vocabulary: {parts}. Tool vocabulary: {tools}.\n"
        f"Split the WHOLE video into consecutive {seconds}-second windows (0-{seconds}, {seconds}-{2 * seconds}, ...) "
        "and label every window. For each window give: start_s, end_s, what is said (short), and a label with "
        "relevant (false for intros, talking heads, sponsor segments, cut-aways), step (short imperative name), "
        "action, tools, parts, visual_state (what the object looks like at the END of the window, concrete enough "
        "to check from one photo), warnings (mistakes, risks or safety notes shown or said), confidence 0-1. "
        "Describe only what is visible or said. Set is_repair_tutorial false if this is not a tutorial for this repair.")


def label_video(client, cfg: dict, url: str) -> tuple[VideoLabels, str]:
    from google.genai import types

    content = types.Content(parts=[types.Part(file_data=types.FileData(file_uri=url)),
                                   types.Part(text=_prompt(cfg, cfg.get("segment_seconds", 10)))])
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=VideoLabels,
                                         media_resolution="MEDIA_RESOLUTION_LOW")
    last = None
    for model in MODELS:
        for attempt in range(2):
            try:
                r = client.models.generate_content(model=model, contents=content, config=config)
                return VideoLabels.model_validate_json(r.text), model
            except Exception as e:  # 503 overload, 429 quota: try again, then the next model
                last = e
                if "503" in str(e) or "429" in str(e):
                    time.sleep(5 * (attempt + 1))
                    continue
                break
    raise RuntimeError(f"no Gemini model could label {url}: {last}")


def _add_source(vid: str, url: str, model: str, cfg: dict) -> None:
    rows = []
    if SOURCES.exists():
        with SOURCES.open(newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r.get("video_id") != vid]
    rows.append({"video_id": vid, "url": url, "creator": "", "repair_type": cfg["repair_id"],
                 "licence": "YouTube - watched via Gemini by URL; not downloaded; derived labels only",
                 "notes": f"labelled by {model}"})
    with SOURCES.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["video_id", "url", "creator", "licence", "repair_type", "notes"])
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description="Label YouTube repair tutorials via Gemini (no downloads)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--discover", type=int, help="find N tutorials with Linkup")
    g.add_argument("--urls", nargs="+", help="YouTube URLs to label")
    ap.add_argument("--force", action="store_true", help="relabel videos already done")
    a = ap.parse_args()

    from google import genai
    from google.genai import types

    from training.pipeline import load_config, make_client, procedure_for_video

    cfg = load_config()
    urls = a.urls or discover(cfg, a.discover)
    print(f"{len(urls)} videos")
    gem = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=300_000))
    claude = make_client(mock=False)
    done = skipped = 0
    with tracking.run("youtube-labels", {"repair_id": cfg["repair_id"], "videos": len(urls)}) as mlf:
        for url in urls:
            vid = f"yt_{video_id(url)}"
            seg_path = WORK / vid / "segments.json"
            if seg_path.exists() and not a.force:
                print(f"   cached: {vid}")
                done += 1
                continue
            try:
                labels, model = label_video(gem, cfg, url)
            except Exception as e:
                print(f"   ! {url}: {e}")
                continue
            if not labels.is_repair_tutorial or not any(w.label.relevant for w in labels.windows):
                print(f"   not a tutorial for this repair: {url}")
                skipped += 1
                continue
            segments = [{"start": w.start_s, "end": w.end_s, "frames": [], "transcript": w.said,
                         "label": w.label.model_dump()} for w in labels.windows]
            seg_path.parent.mkdir(parents=True, exist_ok=True)
            seg_path.write_text(json.dumps(segments, indent=1), encoding="utf-8")
            _add_source(vid, url, model, cfg)
            relevant = sum(w.label.relevant for w in labels.windows)
            print(f"   {vid}: {len(segments)} windows ({relevant} relevant) via {model}")
            procedure_for_video(Path(f"{vid}.mp4"), cfg, claude, segments, mock=False, force=True)
            done += 1
        mlf.log_metrics({"videos_labelled": done, "not_tutorials": skipped})
    print(f"\n{done} videos labelled. Next: python -m training.pipeline merge, then python -m training.adaption_dataset")


if __name__ == "__main__":
    main()
