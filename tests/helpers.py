"""Small test helpers (pytest-asyncio is not installed, so async tests use run())."""
from __future__ import annotations

import asyncio


def run(coro):
    return asyncio.run(coro)


class _Tensorish:
    """The slice of a torch tensor the detector uses: .tolist()."""

    def __init__(self, values):
        self._values = list(values)

    def tolist(self):
        return self._values

    def __len__(self):
        return len(self._values)


class StubBoxes:
    def __init__(self, xyxy, conf, cls, ids=None):
        self.xyxy, self.conf, self.cls = _Tensorish(xyxy), _Tensorish(conf), _Tensorish(cls)
        self.id = None if ids is None else _Tensorish(ids)

    def __len__(self):
        return len(self.xyxy)


class StubResult:
    def __init__(self, boxes=None, names=None):
        self.boxes = boxes
        self.names = names or {0: "bicycle", 1: "person"}


class StubYolo:
    """Stands in for ultralytics.YOLO: returns scripted results and records how it was called."""

    def __init__(self, results=None):
        self.results = list(results or [])     # one StubResult per track() call; empty -> no detections
        self.calls: list[dict] = []

    def track(self, img, persist=False, tracker=None, verbose=False):
        self.calls.append({"persist": persist, "tracker": tracker, "shape": img.shape})
        return [self.results.pop(0) if self.results else StubResult(None)]
