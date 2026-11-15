"""Pre-build the 3D twins of the devices people are most likely to name, so those open instantly at the demo.

Each one is a real run of the live pipeline (product photo from the web, fal cleanup, fal image-to-3D), saved under
data/objects/<product>/ exactly like a run on stage. The twin does not depend on the fault, so any later fault for the same
product reuses it and only the repair plan is learned live. Runs several at once (about 100 s each).

    python scripts/warm_objects.py                 # the default list, against https://localhost:8443
    python scripts/warm_objects.py "Nintendo Switch" "Kindle Paperwhite"
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

BASE = "https://localhost:8443"
DEFAULT = [
    ("Apple AirPods Pro 2", "the right earbud is quiet"), ("Apple AirPods 3", "the left earbud is quieter"),
    ("Sony WH-1000XM5", "the headband is cracked"), ("Bose QuietComfort Ultra Headphones", "the ear cushion is torn"),
    ("Dyson V8", "will not charge"), ("Nintendo Switch", "the joy-con drifts"),
    ("Apple iPhone 13", "the screen is cracked"), ("Apple Watch Series 9", "the battery drains fast"),
    ("Logitech MX Master 3S", "the scroll wheel is stuck"), ("Kindle Paperwhite", "will not charge"),
    ("PlayStation 5 DualSense", "the stick drifts"), ("Samsung Galaxy Buds 2 Pro", "one bud is silent"),
]


def warm(item: tuple[str, str]) -> str:
    name, fault = item
    sid = "warm-" + "".join(c for c in name.lower() if c.isalnum())[:24]
    t0 = time.time()
    with httpx.Client(verify=False, timeout=60) as c:
        c.post(f"{BASE}/coach/object", json={"session": sid, "text": f"{name}, {fault}", "fast": True})
        while time.time() - t0 < 300:
            st = c.get(f"{BASE}/coach/object", params={"session": sid}).json()
            tw = (st.get("phases") or {}).get("twin", {})
            if tw.get("status") == "ready":
                return f"{name}: twin ready in {time.time() - t0:.0f} s"
            if tw.get("status") == "failed":
                return f"{name}: twin failed ({str(tw.get('error'))[:80]})"
            time.sleep(3)
    return f"{name}: no twin after 300 s"


if __name__ == "__main__":
    items = [(n, "will not work") for n in sys.argv[1:]] or DEFAULT
    with ThreadPoolExecutor(4) as p:
        for line in p.map(warm, items):
            print(line, flush=True)
