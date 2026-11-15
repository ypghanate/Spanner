"""Keeping Spanner's spoken lines natural: no narrating the research, and no saying the same thing twice in other words.

Claude writes most of what Spanner says (the answer to a diagnostic question, a reply on the call). Two habits need a hard
stop in code because a prompt alone does not hold them across turns:
  - announcing that it is consulting / checking / reading a manual, guide or instructions (the screen already shows what the
    pipeline is doing, and the person hears it every turn);
  - repeating a point in new words, which an exact-match check never catches.
clean_line() removes such sentences from a line, given what was said before."""
from __future__ import annotations

import re

_STOP = {"the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "and", "or", "it", "its", "that", "this", "for", "on",
         "in", "at", "with", "your", "you", "i", "me", "my", "we", "so", "then", "now", "just", "okay", "ok", "right", "well",
         "let", "lets", "will", "can", "do", "does", "did", "have", "has", "about", "from", "as", "by", "if", "not", "no", "yes"}

# "let me consult the manual", "I'm checking the repair guide for that", "pulling up the instructions", "reading the docs"
_SOURCE = r"(manuals?|guides?|instructions?|documentation|docs|handbook|service notes|repair (info|steps|procedure)|walkthroughs?)"
_LOOKUP_VERB = r"(consult\w*|check\w*|look\w*\s+(it\s+|that\s+|this\s+)?(up|into|through|at)|refer\w*|pull\w*\s+up|go\w*\s+(through|over)|read\w*|review\w*|search\w*|dig\w*|scan\w*|browse\w*|open\w*)"
LOOKUP = re.compile(rf"\b{_LOOKUP_VERB}\b[^.!?]{{0,60}}\b{_SOURCE}\b|\b{_SOURCE}\s+(for|on)\s+(that|this)\b", re.I)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]


def content(text: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split() if len(w) > 2 and w not in _STOP}


def similar(a: str, b: str) -> bool:
    """The same point in other words: most of the shorter sentence's content words are in the other one (at least 3)."""
    x, y = content(a), content(b)
    if min(len(x), len(y)) < 3:
        return a.strip().lower() == b.strip().lower()
    return len(x & y) / min(len(x), len(y)) >= 0.6


def narrates_lookup(sentence: str) -> bool:
    return bool(LOOKUP.search(sentence))


def clean_line(line: str, said: list[str], narrating_ok: bool = False) -> str:
    """The line without sentences that narrate a lookup or repeat something already said. May return ''."""
    heard = [s for old in said[-8:] for s in sentences(old)]
    keep: list[str] = []
    for s in sentences(line):
        if not narrating_ok and narrates_lookup(s):
            continue
        if any(similar(s, h) for h in heard) or any(similar(s, k) for k in keep):
            continue
        keep.append(s)
    return " ".join(keep)


def said_so_far(session: str, history: list[dict] | None = None) -> list[str]:
    """Everything Spanner said in this session: the call's replies plus the object flow's lines."""
    out = [h["content"] for h in (history or []) if h.get("role") == "assistant" and isinstance(h.get("content"), str)]
    try:
        from .objects import _jobs

        job = _jobs.get(session)
        if job:
            out += list(job.said)
    except Exception:
        pass
    return out
