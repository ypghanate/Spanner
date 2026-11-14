"""Training pipeline: repair videos -> labelled segments -> procedures (the knowledge base).

Usage (from the repo root):
  python -m training.pipeline run                 # every video in data/videos/
  python -m training.pipeline run data/videos/vid01.mp4 --mock   # no API calls
  python -m training.pipeline web                 # Tavily web guides -> procedures
  python -m training.pipeline merge               # canonical procedure from videos + web guides
  python -m training.pipeline databricks init     # create the Unity Catalog schema/volume/tables
  python -m training.pipeline databricks sync     # upload everything to Databricks
  python -m training.pipeline schema              # export data/procedures/schema.json

Every command is logged as an MLflow run, and every Claude call is traced (training/tracking.py).

Each video's work goes to data/work/<video_id>/ and every stage is cached, so re-running
resumes where it stopped. After labelling, open data/work/<video_id>/review.html,
spot-check ~20% of the labels, fix mistakes in segments.json, then run
`python -m training.pipeline run <video> --rebuild-procedure`.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import offline  # noqa: E402
from training import databricks_sync, label, tracking, web_scrape  # noqa: E402
from training.schemas import ProcedureRecord, SegmentLabel  # noqa: E402

DATA = ROOT / "data"
VIDEOS_DIR = DATA / "videos"
WORK_DIR = DATA / "work"
WEB_DIR = DATA / "web"
PROCEDURES_DIR = DATA / "procedures"
PER_VIDEO_DIR = PROCEDURES_DIR / "videos"
PER_WEB_DIR = PROCEDURES_DIR / "web"
CONFIG_PATH = ROOT / "training" / "repair.json"
SOURCES_PATH = ROOT / "training" / "sources.csv"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


# ---------------------------------------------------------------- helpers
def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_sources() -> dict[str, dict]:
    if not SOURCES_PATH.exists():
        return {}
    with SOURCES_PATH.open(newline="", encoding="utf-8") as f:
        return {row["video_id"]: row for row in csv.DictReader(f) if row.get("video_id")}


def find_videos(paths: list[str]) -> list[Path]:
    if not paths:
        paths = [str(VIDEOS_DIR)]
    found: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            found += sorted(f for f in p.iterdir() if f.suffix.lower() in VIDEO_EXTS)
        elif p.suffix.lower() in VIDEO_EXTS and p.exists():
            found.append(p)
        else:
            print(f"skip: {p} (not a video)")
    return found


def make_client(mock: bool):
    if mock:
        return None
    import anthropic

    return anthropic.Anthropic()


def pick_frames(frames: list[Path], start: float, end: float, n: int) -> list[tuple[float, Path]]:
    """Up to n frames in [start, end): drop the blurriest, keep the rest evenly spread in time."""
    window = [f for f in frames if start <= offline.frame_time(f) < end]
    if len(window) > n:
        scored = sorted(window, key=offline.sharpness, reverse=True)[: max(n, len(window) * 2 // 3)]
        scored.sort(key=offline.frame_time)
        step = len(scored) / n
        window = [scored[int(i * step)] for i in range(n)]
    return [(offline.frame_time(f), f) for f in window]


def rel(p: Path) -> str:
    return p.resolve().relative_to(ROOT).as_posix()


# ---------------------------------------------------------------- stages
def label_video(video: Path, cfg: dict, client, mock: bool, force: bool, relabel: bool,
                workers: int) -> list[dict]:
    vid = video.stem
    work = WORK_DIR / vid
    print(f"\n== {vid}")

    frames = offline.extract_frames(video, work / "frames", force=force)
    duration = offline.video_duration(video)
    print(f"   {len(frames)} frames, {duration:.0f}s")

    wav = offline.extract_audio(video, work / "audio.wav", force=force)
    transcript = offline.transcribe(wav, work / "transcript.json",
                                    model_size=cfg.get("whisper_model", "small"),
                                    language=cfg.get("language", "en"),
                                    vocabulary=(cfg.get("parts") or []) + (cfg.get("tools") or []),
                                    force=force)
    print(f"   transcript: {len(transcript['words'])} words ({transcript['language'] or 'no audio'})")

    seg_path = work / "segments.json"
    segments: list[dict] = [] if (force or relabel or not seg_path.exists()) else \
        json.loads(seg_path.read_text(encoding="utf-8"))
    done = {s["start"] for s in segments}

    seg_len = cfg.get("segment_seconds", 10)
    windows = [(float(s), float(min(s + seg_len, duration)))
               for s in range(0, int(duration) + 1, seg_len) if s < duration]
    todo = [w for w in windows if w[0] not in done]
    print(f"   segments: {len(windows)} total, {len(todo)} to label")

    def work_one(w):
        start, end = w
        picked = pick_frames(frames, start, end, cfg.get("frames_per_segment", 6))
        text = offline.transcript_slice(transcript, start, end)
        if not picked:
            lab = SegmentLabel(relevant=False, step="", action="", tools=[], parts=[],
                               visual_state="no usable frames", warnings=[], confidence=1.0)
        else:
            lab = label.label_segment(client, cfg, picked, text, start, end, mock=mock)
        return {"start": start, "end": end, "frames": [rel(f) for _, f in picked],
                "transcript": text, "label": lab.model_dump()}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work_one, w): w for w in todo}
        for fut in as_completed(futures):
            try:
                segments.append(fut.result())
            except Exception as e:  # keep going; the window is retried on the next run
                print(f"   ! window {futures[fut][0]:.0f}s failed: {e}")
            segments.sort(key=lambda s: s["start"])
            seg_path.write_text(json.dumps(segments, indent=1), encoding="utf-8")

    write_review(vid, segments, work / "review.html")
    return segments


def procedure_for_video(video: Path, cfg: dict, client, segments: list[dict], mock: bool,
                        force: bool) -> ProcedureRecord | None:
    vid = video.stem
    out = PER_VIDEO_DIR / f"{vid}.json"
    if out.exists() and not force:
        print(f"   procedure: cached ({rel(out)})")
        return ProcedureRecord.model_validate_json(out.read_text(encoding="utf-8"))
    if len(segments) == 0:
        print("   procedure: no labelled segments, skipped")
        return None
    slim = [{"start": s["start"], "end": s["end"], "transcript": s["transcript"], "label": s["label"]}
            for s in segments]
    proc = label.build_procedure(client, cfg, vid, slim, mock=mock)

    frames = sorted((WORK_DIR / vid / "frames").glob("*.jpg"))
    keyframes = {}
    for st in proc.steps:  # representative frame: the last sharp-ish frame of the step
        cands = [f for f in frames if st.start_s <= offline.frame_time(f) <= max(st.start_s, st.end_s - 1)]
        if cands:
            keyframes[str(st.index)] = rel(max(cands[-3:], key=offline.sharpness))

    src = load_sources().get(vid, {})
    record = ProcedureRecord(procedure_id=f"{cfg['repair_id']}__{vid}", repair_id=cfg["repair_id"],
                             source_kind="video", source_videos=[vid], sources=[src] if src else [],
                             keyframes=keyframes, procedure=proc)
    PER_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(record.model_dump_json(indent=1), encoding="utf-8")
    print(f"   procedure: {len(proc.steps)} steps -> {rel(out)}")
    return record


def write_review(vid: str, segments: list[dict], out: Path) -> None:
    """A static page to spot-check labels: frames next to Claude's label, low confidence in red."""
    rows = []
    for s in segments:
        lab = s["label"]
        imgs = "".join(f'<img src="../../../{html.escape(f)}" height="90">' for f in s["frames"])
        low = lab["confidence"] < 0.6 or not lab["relevant"]
        rows.append(
            f'<tr class="{"low" if low else ""}"><td>{s["start"]:.0f}–{s["end"]:.0f}s</td><td>{imgs}</td>'
            f'<td><b>{html.escape(lab["step"]) or "<i>not relevant</i>"}</b><br>{html.escape(lab["action"])}'
            f'<br><small>state: {html.escape(lab["visual_state"])}<br>parts: {html.escape(", ".join(lab["parts"]))}'
            f' · tools: {html.escape(", ".join(lab["tools"]))}<br>warnings: {html.escape("; ".join(lab["warnings"]))}'
            f'</small></td><td>{lab["confidence"]:.2f}</td><td><small>{html.escape(s["transcript"])}</small></td></tr>')
    out.write_text(
        "<!doctype html><meta charset=utf-8><title>Review " + html.escape(vid) + "</title>"
        "<style>body{font-family:sans-serif}td{vertical-align:top;border-bottom:1px solid #ddd;padding:6px}"
        "tr.low{background:#ffe9e9}img{margin-right:3px}</style>"
        f"<h2>Review: {html.escape(vid)}</h2><p>Fix wrong labels in <code>segments.json</code>, then run "
        f"<code>python -m training.pipeline run data/videos/{html.escape(vid)}.* --rebuild-procedure</code>. "
        "Red rows = low confidence or marked not relevant.</p>"
        "<table><tr><th>Time</th><th>Frames</th><th>Label</th><th>Conf.</th><th>Transcript</th></tr>"
        + "".join(rows) + "</table>", encoding="utf-8")


# ---------------------------------------------------------------- commands
def cmd_run(args) -> None:
    offline.require_ffmpeg()
    cfg = load_config()
    client = make_client(args.mock)
    videos = find_videos(args.videos)
    if not videos:
        sys.exit(f"No videos found. Put them in {rel(VIDEOS_DIR)}/ or pass paths.")
    params = {"repair_id": cfg["repair_id"], "label_model": cfg["label_model"], "effort": cfg.get("effort"),
              "segment_seconds": cfg.get("segment_seconds"), "frames_per_segment": cfg.get("frames_per_segment"),
              "whisper_model": cfg.get("whisper_model"), "videos": len(videos), "mock": args.mock}
    with tracking.run("label-videos", params) as mlf:
        all_segments, records = [], []
        for video in videos:
            segments = label_video(video, cfg, client, args.mock, args.force, args.relabel, args.workers)
            all_segments += segments
            rec = procedure_for_video(video, cfg, client, segments, args.mock,
                                      force=args.force or args.relabel or args.rebuild_procedure)
            if rec:
                records.append(rec)
                mlf.log_artifact(str(PER_VIDEO_DIR / f"{video.stem}.json"), "procedures")
        labels = [s["label"] for s in all_segments]
        relevant = [l for l in labels if l["relevant"]]
        mlf.log_metrics({
            "windows": len(labels), "relevant_windows": len(relevant),
            "low_confidence_rate": (sum(l["confidence"] < 0.6 for l in relevant) / len(relevant)) if relevant else 0,
            "avg_confidence": (sum(l["confidence"] for l in relevant) / len(relevant)) if relevant else 0,
            "procedures": len(records), "steps": sum(len(r.procedure.steps) for r in records)})
    print("\nDone. Spot-check the review.html pages in data/work/<video_id>/.")


def cmd_web(args) -> None:
    cfg = load_config()
    client = make_client(args.mock)
    with tracking.run("web-guides", {"repair_id": cfg["repair_id"], "queries": web_scrape.queries(cfg),
                                     "max_results": args.max_results, "mock": args.mock}) as mlf:
        print("Searching the web with Tavily...")
        pages = web_scrape.scrape(cfg, WEB_DIR, max_results=args.max_results, mock=args.mock)
        print(f"   {len(pages)} pages with usable text")
        PER_WEB_DIR.mkdir(parents=True, exist_ok=True)
        kept = 0
        for page in pages:
            out = PER_WEB_DIR / f"{page['page_id']}.json"
            skip_marker = WEB_DIR / f"{page['page_id']}.not_a_guide"
            if (out.exists() or skip_marker.exists()) and not args.force:
                kept += out.exists()
                continue
            try:
                ext = label.procedure_from_web(client, cfg, page, mock=args.mock)
            except Exception as e:
                print(f"   ! {page['url']}: {e}")
                continue
            if not ext.is_repair_guide or not ext.procedure.steps:
                skip_marker.write_text(ext.reason, encoding="utf-8")
                print(f"   not a guide: {page['url']} ({ext.reason})")
                continue
            record = ProcedureRecord(
                procedure_id=f"{cfg['repair_id']}__web_{page['page_id']}", repair_id=cfg["repair_id"],
                source_kind="web", source_videos=[page["page_id"]],
                sources=[{"url": page["url"], "title": page["title"], "kind": "web"}],
                procedure=ext.procedure)
            out.write_text(record.model_dump_json(indent=1), encoding="utf-8")
            mlf.log_artifact(str(out), "procedures")
            kept += 1
            print(f"   guide: {len(ext.procedure.steps)} steps <- {page['url']}")
        mlf.log_metrics({"pages": len(pages), "guides": kept})
    print(f"\n{kept} web guides in {rel(PER_WEB_DIR)}/")


def cmd_merge(args) -> None:
    cfg = load_config()
    test = set(cfg.get("test_videos") or [])
    records = [ProcedureRecord.model_validate_json(p.read_text(encoding="utf-8"))
               for d in (PER_VIDEO_DIR, PER_WEB_DIR) for p in sorted(d.glob("*.json"))]
    train = [r for r in records if r.source_videos[0] not in test]
    if not train:
        sys.exit("No procedures yet. Run `python -m training.pipeline run` and/or `web` first.")
    kinds = {k: sum(r.source_kind == k for r in train) for k in ("video", "web")}
    print(f"Merging {kinds['video']} videos + {kinds['web']} web guides (held out: {sorted(test) or 'none'})")
    with tracking.run("merge-canonical", {"repair_id": cfg["repair_id"], "sources": len(train),
                                          "held_out": sorted(test), "mock": args.mock, **kinds}) as mlf:
        merged = label.merge_procedures(make_client(args.mock), cfg,
                                        {r.procedure_id: r.procedure for r in train}, mock=args.mock)
        record = ProcedureRecord(procedure_id=cfg["repair_id"], repair_id=cfg["repair_id"],
                                 source_kind="canonical", source_videos=[r.source_videos[0] for r in train],
                                 sources=[s for r in train for s in r.sources], procedure=merged)
        out = PROCEDURES_DIR / f"{cfg['repair_id']}.json"
        out.write_text(record.model_dump_json(indent=1), encoding="utf-8")
        mlf.log_artifact(str(out), "procedures")
        mlf.log_metrics({"canonical_steps": len(merged.steps),
                         "known_mistakes": sum(len(s.common_mistakes) for s in merged.steps)})
    print(f"Canonical procedure: {len(merged.steps)} steps -> {rel(out)}")


def cmd_databricks(args) -> None:
    if args.action == "init":
        databricks_sync.init()
        return
    cfg = load_config()
    with tracking.run("databricks-sync", {"repair_id": cfg["repair_id"], "videos": args.videos}) as mlf:
        counts = databricks_sync.sync(cfg, include_videos=args.videos)
        mlf.log_metrics({f"rows_{t}": n for t, n in counts.items()})
    if databricks_sync.remote():
        print("\nNext: `cd databricks && databricks bundle run training_refresh` "
              "(quality pipeline + knowledge-base index).")


def cmd_schema(_args) -> None:
    out = PROCEDURES_DIR / "schema.json"
    PROCEDURES_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ProcedureRecord.model_json_schema(), indent=1), encoding="utf-8")
    print(f"Wrote {rel(out)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Spanner training pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="extract, transcribe, label and build procedures")
    r.add_argument("videos", nargs="*", help="video files or folders (default: data/videos/)")
    r.add_argument("--mock", action="store_true", help="no API calls, fake labels")
    r.add_argument("--force", action="store_true", help="redo every stage")
    r.add_argument("--relabel", action="store_true", help="redo Claude labels, keep frames/transcript")
    r.add_argument("--rebuild-procedure", action="store_true", help="rebuild the procedure from segments.json")
    r.add_argument("--workers", type=int, default=int(os.getenv("LABEL_WORKERS", "4")))
    r.set_defaults(func=cmd_run)

    wb = sub.add_parser("web", help="find web repair guides with Tavily and extract procedures")
    wb.add_argument("--mock", action="store_true", help="no API calls, fake pages")
    wb.add_argument("--force", action="store_true", help="re-read pages already processed")
    wb.add_argument("--max-results", type=int, default=5, help="results per search query")
    wb.set_defaults(func=cmd_web)

    m = sub.add_parser("merge", help="canonical procedure from training videos + web guides")
    m.add_argument("--mock", action="store_true")
    m.set_defaults(func=cmd_merge)

    d = sub.add_parser("databricks", help="Unity Catalog setup and upload")
    d.add_argument("action", choices=["init", "sync"])
    d.add_argument("--videos", action="store_true", help="also upload the raw videos to the Volume")
    d.set_defaults(func=cmd_databricks)

    s = sub.add_parser("schema", help="export data/procedures/schema.json")
    s.set_defaults(func=cmd_schema)

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
