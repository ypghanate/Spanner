"""Compare Pass 1 backends (Claude Haiku vs the Adaption fine-tune) on the held-out eval set.

    uv run python training/eval.py                      # both backends (Adaption only if configured)
    uv run python training/eval.py --backends claude    # Claude row only, still useful for the pitch

Reads training/data/eval_gold.jsonl (written by make_dataset.py). For each backend it reports:
valid-JSON rate, intent accuracy vs gold, fault match, latency p50/p95. Writes training/RESULTS.md
and training/results.svg and prints PASS or FAIL for turning Adaption on:

    PASS = Adaption valid JSON >= 98%  AND  Adaption intent accuracy within 3 points of Claude.

The Adaption backend is called directly (no Claude fallback), so its failures show up as invalid.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.brain import pass1, pass1_adaption  # noqa: E402
from backend.brain.pass1 import Pass1Input  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.contracts import SituationReport  # noqa: E402

VALID_JSON_GATE = 0.98
INTENT_GAP_GATE = 0.03          # Adaption must be within 3 points of Claude
BACKEND_LABELS = {"claude": "Claude Haiku 5.5", "adaption": "Adaption fine-tune"}
Backend = Callable[[Pass1Input], Awaitable[SituationReport]]


@dataclass
class EvalRow:
    id: str
    intent: str
    fault: str | None
    inp: Pass1Input


@dataclass
class Metrics:
    backend: str
    n: int = 0
    valid: int = 0
    intent_correct: int = 0
    fault_total: int = 0
    fault_correct: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def valid_rate(self) -> float:
        return self.valid / self.n if self.n else 0.0

    @property
    def intent_acc(self) -> float:
        return self.intent_correct / self.n if self.n else 0.0

    @property
    def fault_rate(self) -> float | None:
        return self.fault_correct / self.fault_total if self.fault_total else None

    def pct(self, ms: float) -> float:
        xs = sorted(self.latencies_ms)
        if not xs:
            return 0.0
        k = (len(xs) - 1) * ms
        lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
        return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)

    @property
    def p50(self) -> float:
        return self.pct(0.5)

    @property
    def p95(self) -> float:
        return self.pct(0.95)


def fault_matches(pred: str | None, gold: str | None) -> bool:
    """Case-insensitive containment, either way. Both must be non-empty."""
    if not pred or not gold:
        return False
    p, g = pred.strip().lower(), gold.strip().lower()
    return bool(p) and bool(g) and (p in g or g in p)


def load_rows(path: Path, limit: int | None = None) -> list[EvalRow]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows.append(EvalRow(r["id"], r["intent"], r.get("fault"), Pass1Input.model_validate(r["input"])))
    return rows[:limit] if limit else rows


async def evaluate(name: str, backend: Backend, rows: list[EvalRow], concurrency: int = 1) -> Metrics:
    m = Metrics(backend=name, n=len(rows))
    sem = asyncio.Semaphore(concurrency)

    async def one(row: EvalRow) -> None:
        async with sem:
            t0 = time.perf_counter()
            try:
                report = await backend(row.inp)
                ok = True
            except Exception as e:  # any failure counts as invalid output
                report, ok = None, False
                m.errors.append(f"{row.id}: {type(e).__name__}: {e}")
            m.latencies_ms.append((time.perf_counter() - t0) * 1000)
        if ok and report is not None:
            m.valid += 1
            m.intent_correct += report.user_intent == row.intent
        if row.fault:
            m.fault_total += 1
            m.fault_correct += bool(ok and report is not None and fault_matches(report.suspected_fault, row.fault))

    await asyncio.gather(*(one(r) for r in rows))
    return m


def verdict(claude: Metrics | None, adaption: Metrics | None) -> tuple[str, str]:
    """('PASS' | 'FAIL' | 'N/A', explanation)."""
    if adaption is None:
        return "N/A", "Adaption was not evaluated, so the switch stays on Claude (PASS1_BACKEND=claude)."
    if claude is None:
        return "N/A", "Claude was not evaluated, so there is no baseline to compare Adaption against."
    valid_ok = adaption.valid_rate >= VALID_JSON_GATE
    gap = claude.intent_acc - adaption.intent_acc
    intent_ok = gap <= INTENT_GAP_GATE + 1e-9
    lines = [
        f"- Valid JSON {adaption.valid_rate:.1%} (needs at least {VALID_JSON_GATE:.0%}): {'ok' if valid_ok else 'FAILS'}",
        f"- Intent accuracy {adaption.intent_acc:.1%} vs Claude {claude.intent_acc:.1%}, gap {gap * 100:+.1f} points "
        f"(needs at most {INTENT_GAP_GATE * 100:.0f}): {'ok' if intent_ok else 'FAILS'}",
    ]
    if valid_ok and intent_ok:
        return "PASS", "\n".join(lines) + "\n\nSet `PASS1_BACKEND=adaption` in `.env`. Claude stays as the automatic fallback."
    return "FAIL", "\n".join(lines) + "\n\nKeep `PASS1_BACKEND=claude`. The eval numbers are still worth pitching."


def _fmt_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def render_markdown(results: dict[str, Metrics], n: int, v: tuple[str, str], when: str) -> str:
    lines = [
        "# Pass 1 eval: Claude Haiku vs the Adaption fine-tune",
        "",
        f"Run {when} on {n} held-out scenarios (`training/data/eval_gold.jsonl`, stratified over the six intents).",
        "",
        "| Backend | Valid JSON | Intent accuracy | Fault match | Latency p50 | Latency p95 |",
        "|---|---|---|---|---|---|",
    ]
    for name, m in results.items():
        lines.append(f"| {BACKEND_LABELS.get(name, name)} | {_fmt_pct(m.valid_rate)} | {_fmt_pct(m.intent_acc)} | "
                     f"{_fmt_pct(m.fault_rate)} | {m.p50:.0f} ms | {m.p95:.0f} ms |")
    lines += [
        "",
        "Valid JSON counts a call that returned a well-formed `SituationReport`; errors and timeouts count as invalid. "
        "Intent accuracy is over all rows (an invalid answer is wrong). Fault match is over rows where the scenario "
        "shows a fault: the predicted `suspected_fault` must contain, or be contained in, the gold fault (case-insensitive).",
        "",
        f"## Verdict: {v[0]}",
        "",
        v[1],
        "",
        "![Pass 1 eval](results.svg)",
        "",
    ]
    for name, m in results.items():
        if m.errors:
            lines += [f"<details><summary>{BACKEND_LABELS.get(name, name)}: {len(m.errors)} failed calls</summary>", ""]
            lines += [f"- {e}" for e in m.errors[:10]]
            lines += ["", "</details>", ""]
    return "\n".join(lines)


def render_svg(results: dict[str, Metrics]) -> str:
    colors = {"claude": "#00B8D4", "adaption": "#1DB87A"}
    groups = [("Valid JSON", lambda m: m.valid_rate), ("Intent accuracy", lambda m: m.intent_acc),
              ("Fault match", lambda m: m.fault_rate)]
    w, h, left, top, bottom = 640, 380, 56, 40, 90
    plot_h = h - top - bottom
    group_w = (w - left - 24) / len(groups)
    bar_w = 46
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
           'font-family="Inter, Arial, sans-serif">',
           f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
           f'<text x="{left}" y="24" font-size="15" font-weight="600" fill="#1a1a1a">Pass 1 eval (higher is better)</text>']
    for pct in (0, 25, 50, 75, 100):
        y = top + plot_h * (1 - pct / 100)
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{w - 16}" y2="{y:.1f}" stroke="#e3e3e3"/>')
        out.append(f'<text x="{left - 8}" y="{y + 4:.1f}" font-size="11" text-anchor="end" fill="#666">{pct}%</text>')
    names = list(results)
    for gi, (label, get) in enumerate(groups):
        gx = left + gi * group_w + (group_w - len(names) * (bar_w + 8)) / 2
        for bi, name in enumerate(names):
            val = get(results[name])
            x = gx + bi * (bar_w + 8)
            if val is None:
                out.append(f'<text x="{x + bar_w / 2:.1f}" y="{top + plot_h - 6}" font-size="11" text-anchor="middle" fill="#999">n/a</text>')
                continue
            bh = max(0.0, min(1.0, val)) * plot_h
            y = top + plot_h - bh
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w}" height="{bh:.1f}" rx="3" fill="{colors.get(name, "#888")}"/>')
            out.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 5:.1f}" font-size="12" text-anchor="middle" fill="#1a1a1a">{val * 100:.1f}%</text>')
        out.append(f'<text x="{left + gi * group_w + group_w / 2:.1f}" y="{top + plot_h + 20}" font-size="12" text-anchor="middle" fill="#333">{escape(label)}</text>')
    lx = left
    for name in names:
        out.append(f'<rect x="{lx}" y="{h - 52}" width="12" height="12" rx="2" fill="{colors.get(name, "#888")}"/>')
        m = results[name]
        text = f'{BACKEND_LABELS.get(name, name)}: p50 {m.p50:.0f} ms, p95 {m.p95:.0f} ms'
        out.append(f'<text x="{lx + 18}" y="{h - 42}" font-size="12" fill="#333">{escape(text)}</text>')
        lx += 290
    out.append(f'<text x="{left}" y="{h - 16}" font-size="11" fill="#777">n = {next(iter(results.values())).n} held-out scenarios</text>')
    out.append("</svg>")
    return "\n".join(out)


async def run_eval(rows: list[EvalRow], backends: dict[str, Backend], concurrency: dict[str, int] | None = None) -> dict[str, Metrics]:
    concurrency = concurrency or {}
    out: dict[str, Metrics] = {}
    for name, fn in backends.items():
        print(f"evaluating {BACKEND_LABELS.get(name, name)} on {len(rows)} rows ...")
        out[name] = await evaluate(name, fn, rows, concurrency.get(name, 1))
    return out


def adaption_configured() -> bool:
    s = get_settings()
    return bool(s.adaption_model_id and s.together_api_key)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    d = get_settings().training_data_dir
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", type=Path, default=d / "eval_gold.jsonl")
    ap.add_argument("--backends", default="claude,adaption", help="comma list of: claude, adaption")
    ap.add_argument("--limit", type=int, default=None, help="only the first N rows (quick check)")
    ap.add_argument("--concurrency", type=int, default=1, help="parallel calls per backend (1 gives honest latency)")
    ap.add_argument("--results", type=Path, default=ROOT / "training" / "RESULTS.md")
    ap.add_argument("--chart", type=Path, default=ROOT / "training" / "results.svg")
    return ap.parse_args(argv)


async def main(argv: list[str] | None = None, backends: dict[str, Backend] | None = None) -> int:
    a = parse_args(argv)
    if not a.gold.exists():
        print(f"{a.gold} not found. Run training/make_dataset.py --yes first.", file=sys.stderr)
        return 2
    rows = load_rows(a.gold, a.limit)
    if not rows:
        print("eval set is empty", file=sys.stderr)
        return 2

    if backends is None:
        wanted = [b.strip() for b in a.backends.split(",") if b.strip()]
        backends = {}
        if "claude" in wanted:
            if not get_settings().anthropic_api_key:
                print("ANTHROPIC_API_KEY is not set; cannot evaluate Claude.", file=sys.stderr)
                return 2
            backends["claude"] = pass1.call_claude
        if "adaption" in wanted:
            if adaption_configured():
                backends["adaption"] = pass1_adaption.run_adaption
            else:
                print("Adaption skipped: set ADAPTION_MODEL_ID and TOGETHER_API_KEY once the model is trained.")
    if not backends:
        print("no backend to evaluate", file=sys.stderr)
        return 2

    results = await run_eval(rows, backends, {k: a.concurrency for k in backends})
    v = verdict(results.get("claude"), results.get("adaption"))
    when = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    a.results.write_text(render_markdown(results, len(rows), v, when), encoding="utf-8")
    a.chart.write_text(render_svg(results), encoding="utf-8")

    print()
    print(f"{'backend':<20}{'valid JSON':>12}{'intent acc':>12}{'fault match':>13}{'p50 ms':>9}{'p95 ms':>9}")
    for name, m in results.items():
        print(f"{BACKEND_LABELS.get(name, name):<20}{_fmt_pct(m.valid_rate):>12}{_fmt_pct(m.intent_acc):>12}"
              f"{_fmt_pct(m.fault_rate):>13}{m.p50:>9.0f}{m.p95:>9.0f}")
    print(f"\nVERDICT: {v[0]}\n{v[1]}")
    print(f"\nwrote {a.results} and {a.chart}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
