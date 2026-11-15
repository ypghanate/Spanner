"""3D for the demo object: a procedurally generated Royal Kludge RK68 with every part separate.

    python -m training.scene3d [--key F]     # writes data/models/rk68/{rk68.glb, pack.json, demo_plan.json}

- rk68.glb       true-to-spec RK68 (316 x 105 x 39 mm, 68-key ANSI 65%): every keycap, switch, hot-swap
                 socket, LED, stabilizer, the plate, PCB, battery, case, USB-C port and mode switch is its
                 own named node, so the app can highlight, explode and reassemble any of them.
- pack.json      part vocabulary -> nodes, groups, explode offsets per layer, key rows (keycap profile),
                 camera views, real specs with source URLs (from Linkup, data/models/rk68/specs.json).
- demo_plan.json the knowledge-base procedure as a contracts.RepairPlan with 3D commands on every step
                 (+ the Linkup replacement card when one exists), so the brain/app can play the repair
                 step by step. training/viewer/ plays it with animated tools, voice and a Jarvis HUD.
Units: 1 = 1 cm. y is up, x to the right, z towards the user (front edge).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "models" / "rk68"
U = 1.905                      # one key unit (19.05 mm)
WIDTH, DEPTH = 16 * U, 5 * U   # key area: 30.48 x 9.525 cm (65%: 16 units wide)
CASE_W, CASE_D, CASE_H = 31.6, 10.5, 2.0   # RK68 spec: about 316 x 105 mm (x 39 mm with keycaps + feet)
WALL_X, WALL_Z = (CASE_W - WIDTH) / 2, (CASE_D - DEPTH) / 2
Y = {"floor": 0.25, "battery": 0.70, "pcb": 1.00, "socket": 1.15, "led": 1.10, "plate": 1.45,
     "stab": 1.60, "switch": 1.85, "keycap": 2.90}

# The RK68 (ANSI 65%) layout: (id, label, width in units), 68 keys, 16 units wide. The right column (Del, PgUp, PgDn,
# End) and the arrow cluster are what the RK61 does not have; the right Shift is 1.75u to make room for the up arrow.
LAYOUT = [
    [("ESC", "Esc", 1)] + [(c, c, 1) for c in "1234567890"] + [("MINUS", "-", 1), ("EQUAL", "=", 1),
                                                              ("BACKSPACE", "Backspace", 2), ("DEL", "Del", 1)],
    [("TAB", "Tab", 1.5)] + [(c, c, 1) for c in "QWERTYUIOP"] + [("LBRACKET", "[", 1), ("RBRACKET", "]", 1),
                                                                ("BACKSLASH", "\\", 1.5), ("PGUP", "PgUp", 1)],
    [("CAPS", "Caps", 1.75)] + [(c, c, 1) for c in "ASDFGHJKL"] + [("SEMICOLON", ";", 1), ("QUOTE", "'", 1),
                                                                  ("ENTER", "Enter", 2.25), ("PGDN", "PgDn", 1)],
    [("LSHIFT", "Shift", 2.25)] + [(c, c, 1) for c in "ZXCVBNM"] + [("COMMA", ",", 1), ("PERIOD", ".", 1),
                                                                   ("SLASH", "/", 1), ("RSHIFT", "Shift", 1.75),
                                                                   ("UP", "\u2191", 1), ("END", "End", 1)],
    [("LCTRL", "Ctrl", 1.25), ("WIN", "Win", 1.25), ("LALT", "Alt", 1.25), ("SPACE", "Space", 6.25),
     ("RALT", "Alt", 1), ("FN", "Fn", 1), ("RCTRL", "Ctrl", 1), ("LEFT", "\u2190", 1), ("DOWN", "\u2193", 1), ("RIGHT", "\u2192", 1)],
]
assert sum(len(r) for r in LAYOUT) == 68
assert all(abs(sum(w for _, _, w in r) - 16) < 1e-6 for r in LAYOUT), "every RK68 row is 16 units wide"

COLORS = {  # RGBA 0-255: black RK68 with RK Red switches
    "keycap": (38, 40, 46, 255), "switch": (20, 20, 22, 255), "stem": (210, 40, 45, 255),
    "stabilizer": (200, 200, 205, 255), "plate": (55, 58, 64, 255), "pcb": (22, 60, 40, 255),
    "socket": (30, 30, 32, 255), "led": (230, 240, 255, 255), "case": (30, 31, 35, 255),
    "usb": (180, 180, 185, 255), "battery": (120, 125, 130, 255), "mode": (60, 60, 65, 255),
}
# How far each layer lifts in the exploded view (cm), top layers furthest
EXPLODE = {"keycaps": 9.0, "stabilizers": 5.0, "switches": 6.2, "plate": 3.6, "hotswap_sockets": 2.4,
           "leds": 2.4, "pcb": 1.6, "battery": 0.7, "case": 0.0, "usb_c_port": 0.0, "mode_switch": 0.0}


def _box(w, h, d, center, color):
    import trimesh

    m = trimesh.creation.box(extents=(w, h, d))
    m.apply_translation(center)
    m.visual.face_colors = color
    return m


def _keycap(w_units, center):
    import numpy as np

    m = _box(w_units * U - 0.15, 0.8, U - 0.15, (0, 0, 0), COLORS["keycap"])
    top = m.vertices[:, 1] > 0            # a slightly tapered cap
    m.vertices[top, 0] *= (w_units * U - 0.45) / (w_units * U - 0.15)
    m.vertices[top, 2] *= 0.8
    m.apply_translation(np.array(center))
    return m


def build() -> dict:
    import trimesh

    scene = trimesh.Scene()
    nodes: dict[str, list[str]] = {g: [] for g in EXPLODE}
    keys = {}

    def add(mesh, name, group):
        scene.add_geometry(mesh, node_name=name, geom_name=name)
        nodes.setdefault(group, []).append(name)

    for row, keys_in_row in enumerate(LAYOUT):
        x = 0.0
        z = row * U + U / 2
        for kid, label, w in keys_in_row:
            cx, cz = x + w * U / 2 - WIDTH / 2, z - DEPTH / 2
            keys[kid] = {"label": label, "width_u": w, "row": row, "x": round(cx, 3), "z": round(cz, 3)}
            add(_keycap(w, (cx, Y["keycap"], cz)), f"keycap_{kid}", "keycaps")
            sw = trimesh.util.concatenate([_box(1.4, 1.1, 1.4, (cx, Y["switch"], cz), COLORS["switch"]),
                                           _box(0.4, 0.35, 0.4, (cx, Y["switch"] + 0.7, cz), COLORS["stem"])])
            add(sw, f"switch_{kid}", "switches")
            add(_box(0.9, 0.25, 0.5, (cx, Y["socket"], cz + 0.3), COLORS["socket"]), f"socket_{kid}", "hotswap_sockets")
            add(_box(0.2, 0.08, 0.2, (cx, Y["led"], cz - 0.45), COLORS["led"]), f"led_{kid}", "leds")
            if w >= 2:
                add(_box(w * U - 1.2 * U, 0.12, 0.25, (cx, Y["stab"], cz + 0.55), COLORS["stabilizer"]),
                    f"stabilizer_{kid}", "stabilizers")
            x += w * U

    add(_box(WIDTH, 0.15, DEPTH, (0, Y["plate"], 0), COLORS["plate"]), "plate", "plate")
    add(_box(WIDTH, 0.16, DEPTH, (0, Y["pcb"], 0), COLORS["pcb"]), "pcb", "pcb")
    add(_box(6.5, 0.3, 2.8, (-5.0, Y["battery"], 0.3), COLORS["battery"]), "battery", "battery")
    case = trimesh.util.concatenate([
        _box(CASE_W, 0.5, CASE_D, (0, Y["floor"], 0), COLORS["case"]),
        _box(CASE_W, CASE_H, WALL_Z, (0, CASE_H / 2, -DEPTH / 2 - WALL_Z / 2), COLORS["case"]),
        _box(CASE_W, CASE_H, WALL_Z, (0, CASE_H / 2, DEPTH / 2 + WALL_Z / 2), COLORS["case"]),
        _box(WALL_X, CASE_H, DEPTH, (-WIDTH / 2 - WALL_X / 2, CASE_H / 2, 0), COLORS["case"]),
        _box(WALL_X, CASE_H, DEPTH, (WIDTH / 2 + WALL_X / 2, CASE_H / 2, 0), COLORS["case"])])
    add(case, "case", "case")
    back_z = -CASE_D / 2
    add(_box(0.9, 0.35, 0.3, (-WIDTH / 2 + 2.5, 1.15, back_z - 0.1), COLORS["usb"]), "usb_c_port", "usb_c_port")
    add(_box(0.8, 0.3, 0.3, (WIDTH / 2 - 2.5, 1.15, back_z - 0.1), COLORS["mode"]), "mode_switch", "mode_switch")

    OUT.mkdir(parents=True, exist_ok=True)
    scene.export(OUT / "rk68.glb")
    specs_file = OUT / "specs.json"
    specs = json.loads(specs_file.read_text(encoding="utf-8")) if specs_file.exists() else {}
    pack = {
        "object": "Royal Kludge RK68", "model_file": "rk68.glb", "units": "cm",
        "layout": "ANSI 65%, 68 keys", "dimensions_cm": [CASE_W, 3.9, CASE_D], "key_unit_cm": U,
        "case_cm": [CASE_W, CASE_H, CASE_D], "key_area_cm": [WIDTH, DEPTH], "wall_cm": [WALL_X, WALL_Z],
        "layers_y": Y, "ports": {"usb_c_port": [-WIDTH / 2 + 2.5, 1.15, back_z - 0.1],
                                 "mode_switch": [WIDTH / 2 - 2.5, 1.15, back_z - 0.1]},
        "groups": nodes, "explode_offsets_cm": EXPLODE, "keys": keys,
        "parts": {  # repair.json part vocabulary -> nodes/groups
            "keycap": "keycaps", "mechanical switch": "switches", "switch": "switches",
            "hot-swap socket": "hotswap_sockets", "socket": "hotswap_sockets", "switch pins": "switches",
            "plate": "plate", "PCB": "pcb", "stabilizer": "stabilizers", "space bar": "keycap_SPACE",
            "case": "case", "USB-C port": "usb_c_port", "battery": "battery", "LED": "leds",
            "backlight": "leds", "mode switch": "mode_switch"},
        "views": {"iso": [0, 22, 30], "top": [0, 36, 0.1], "side": [34, 8, 0], "front": [0, 6, 34],
                  "back": [0, 10, -32], "detail": [3.5, 8, 10], "socket": [0.5, 6, 4]},
        "specs": specs,
    }
    (OUT / "pack.json").write_text(json.dumps(pack, indent=1), encoding="utf-8")
    return pack


# ---------------------------------------------------------------- steps -> kind -> 3D commands
# One classifier for every step wording: the contract commands (below) and the viewer's choreography
# (training/viewer/viewer.js) both key off the kind, so they can't drift apart.
KINDS = [
    ("cable-out",      r"disconnect|unplug|powered? (down|off)|remove the (usb|cable)", r"plug .*back|reconnect"),
    ("cable-in-test",  r"plug .*back|reconnect|keyboard tester|registers|press the (repaired|fixed|new) key|test the key", None),
    ("keycap-pull",    r"keycap.*(pull(?!er)|lift|remov|take .*off)|puller.*keycap.*pull", None),
    ("keycap-place",   r"keycap.*(press|seat|line up|put .*back|install|refit)", None),
    ("new-switch-inspect", r"(spare|new|compatible|replacement) switch.*(\bpins?\b|parallel|springs back|confirm)", None),
    ("switch-align",   r"hold the switch|hover|line (its |the )?pins up|lower(ing)? (it|the switch)|align the (pins|switch)", None),
    ("switch-insert",  r"(press|push) (it |the switch |down )?(straight )?down|clicks? (flush|into)|until it (clicks|seats)|insert", None),
    ("switch-test",    r"press the (switch )?stem|springs back|wobble", None),
    ("puller-place",   r"puller.*(place|position|put|rest).*(prong|clip)|place the (switch )?puller", r"squeez|pull the switch|pull straight up"),
    ("switch-pull",    r"squeez.*switch|switch.*squeez|pull (the switch |it )?straight up|pull the switch|extract the switch|lift (it|the switch) out", None),
    ("socket-inspect", r"socket.*(check|inspect|look|debris|dust|damaged|seated|clear)", None),
    ("stabilizer",     r"stabiliz|space bar", None),
]


def step_kind(text: str, parts: list[str]) -> str:
    t = f"{text} {' '.join(parts)}".lower()
    for kind, yes, no in KINDS:
        if re.search(yes, t) and not (no and re.search(no, t)):
            return kind
    return "other"


KIND_NAMES = [k for k, _, _ in KINDS] + ["other"]


def scene3d_for_step(text: str, parts: list[str], key: str = "F") -> list[dict]:
    """3D commands (contracts.SceneCommand) for one repair step. `key` is the broken key."""
    return scene3d_for_kind(step_kind(text, parts), parts, key)


def scene3d_for_kind(kind: str, parts: list[str], key: str = "F") -> list[dict]:
    """Same, when the kind is already known (Claude-generated plans choose it per step)."""
    cap, sw, sock = f"keycap_{key}", f"switch_{key}", f"socket_{key}"
    stab_cap = cap if key in ("SPACE", "ENTER", "BACKSPACE", "LSHIFT", "RSHIFT") else "keycap_SPACE"
    table = {
        "cable-out": [{"op": "rotate_to", "view": "back"}, {"op": "highlight", "part": "usb_c_port"},
                      {"op": "arrow", "part": "usb_c_port", "direction": "out"}],
        "cable-in-test": [{"op": "reset"}, {"op": "rotate_to", "view": "iso"}, {"op": "pulse", "part": cap}],
        "keycap-pull": [{"op": "rotate_to", "view": "detail"}, {"op": "highlight", "part": cap},
                        {"op": "arrow", "part": cap, "direction": "up"}],
        "keycap-place": [{"op": "rotate_to", "view": "detail"}, {"op": "highlight", "part": cap},
                         {"op": "arrow", "part": cap, "direction": "down"}],
        "new-switch-inspect": [{"op": "rotate_to", "view": "detail"}, {"op": "highlight", "part": sw}],
        "switch-align": [{"op": "rotate_to", "view": "detail"}, {"op": "highlight", "part": sock},
                         {"op": "arrow", "part": sw, "direction": "down"}],
        "switch-insert": [{"op": "rotate_to", "view": "detail"}, {"op": "highlight", "part": sw},
                          {"op": "arrow", "part": sw, "direction": "down"}],
        "switch-test": [{"op": "rotate_to", "view": "detail"}, {"op": "pulse", "part": sw}],
        "puller-place": [{"op": "rotate_to", "view": "detail"}, {"op": "highlight", "part": sw}],
        "switch-pull": [{"op": "rotate_to", "view": "detail"}, {"op": "highlight", "part": sw},
                        {"op": "arrow", "part": sw, "direction": "up"}],
        "socket-inspect": [{"op": "rotate_to", "view": "socket"}, {"op": "highlight", "part": sock}],
        "stabilizer": [{"op": "rotate_to", "view": "detail"}, {"op": "highlight", "part": stab_cap},
                       {"op": "arrow", "part": stab_cap, "direction": "up"}],
    }
    if kind in table:
        return table[kind]
    mapped = [p for p in parts if p]
    return ([{"op": "highlight", "part": mapped[0]}] if mapped else []) + [{"op": "show_step"}]


def latest_card_view() -> dict | None:
    """The newest Linkup replacement card for the RK61, as the app renders it (for the viewer)."""
    from backend.parts_agent import ReplacementCard, card_view

    cards = sorted((ROOT / "data" / "parts").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in cards:
        card = ReplacementCard.model_validate_json(p.read_text(encoding="utf-8"))
        if "rk61" in card.identity.name().lower().replace(" ", "") and not card.mock:
            return card_view(card)
    return None


def demo_plan(key: str) -> dict | None:
    from backend.contracts import SceneCommand
    from backend.knowledge import plan_from_knowledge

    plan = plan_from_knowledge()
    if plan is None:
        return None
    for step in plan.steps:
        step.scene3d = [SceneCommand(**c) for c in scene3d_for_step(step.instruction, step.parts, key)]
    out = plan.model_dump()
    for step in out["steps"]:  # extra key for the viewer's choreography (the app's RepairStep ignores it)
        step["kind"] = step_kind(step["instruction"], step["parts"])
    return out


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Generate the RK61 3D model, pack and demo plan")
    ap.add_argument("--key", default="F", help="the broken key in the demo (layout id, e.g. F, SPACE)")
    a = ap.parse_args()
    pack = build()
    n = sum(len(v) for v in pack["groups"].values())
    print(f"rk61.glb: {n} named parts ({len(pack['groups']['keycaps'])} keycaps, {len(pack['groups']['switches'])} "
          f"switches, {len(pack['groups']['hotswap_sockets'])} sockets, {len(pack['groups']['leds'])} LEDs) "
          f"-> {OUT.relative_to(ROOT)}; specs: {len(pack['specs'].get('specs', []))}")
    plan = demo_plan(a.key)
    if plan:
        card = latest_card_view()
        (OUT / "demo_plan.json").write_text(json.dumps({"broken_key": a.key, "plan": plan, "parts_card": card},
                                                       indent=1), encoding="utf-8")
        for s in plan["steps"]:
            print(f"  step {s['index']}: " + ", ".join(c["op"] + ":" + (c.get("part") or c.get("group") or c.get("view") or "")
                                                     for c in s["scene3d"]))
        print(f"  parts card: {'yes (' + str(len(card['options'])) + ' offers)' if card else 'none yet'}")
    else:
        print("  (no canonical procedure yet: run `python -m training.pipeline merge`, then rerun this)")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------- a pack for ANY keyboard layout (read from the camera)
_KEY_IDS = {"esc": "ESC", "escape": "ESC", "backspace": "BACKSPACE", "bksp": "BACKSPACE", "tab": "TAB", "caps": "CAPS",
            "capslock": "CAPS", "caps lock": "CAPS", "enter": "ENTER", "return": "ENTER", "shift": "SHIFT", "ctrl": "CTRL",
            "control": "CTRL", "alt": "ALT", "win": "WIN", "windows": "WIN", "cmd": "WIN", "fn": "FN", "menu": "MENU",
            "del": "DEL", "delete": "DEL", "ins": "INS", "insert": "INS", "home": "HOME", "end": "END", "pgup": "PGUP",
            "page up": "PGUP", "pgdn": "PGDN", "page down": "PGDN", "space": "SPACE", "prtsc": "PRTSC", "scrlk": "SCRLK",
            "pause": "PAUSE", "\\": "BACKSLASH", "[": "LBRACKET", "]": "RBRACKET", ";": "SEMICOLON", "'": "QUOTE", ",": "COMMA",
            ".": "PERIOD", "/": "SLASH", "-": "MINUS", "=": "EQUAL", "`": "BACKTICK", "~": "BACKTICK",
            "\u2191": "UP", "\u2193": "DOWN", "\u2190": "LEFT", "\u2192": "RIGHT"}
_PAIRED = {"SHIFT", "CTRL", "ALT", "WIN"}


def layout_rows_clean(rows: list[list[tuple[str, float]]]) -> list[list[tuple[str, str, float]]]:
    """Camera-read rows [(legend, width_u)] -> [(id, label, width_u)] with unique ids and every row the same width."""
    out, seen = [], {}
    for r in rows:
        cleaned = []
        for legend, w in r:
            label = (legend or "").strip() or "?"
            base = _KEY_IDS.get(label.lower()) or re.sub(r"[^A-Z0-9]", "", label.upper()) or "KEY"
            cleaned.append([base, label, max(0.75, min(float(w or 1), 7.0))])
        out.append(cleaned)
    target = max(sum(w for _, _, w in r) for r in out)
    for r in out:
        diff = target - sum(w for _, _, w in r)
        if abs(diff) > 0.01:
            widest = max(r, key=lambda k: k[2]); widest[2] = round(max(0.75, widest[2] + diff), 3)
    for r in out:                                         # unique ids: paired keys are left/right, other repeats numbered
        for k in r:
            seen.setdefault(k[0], []).append(k)
    for base, ks in seen.items():
        if len(ks) > 1:
            for i, k in enumerate(ks):
                k[0] = (("L" if i == 0 else "R" if i == 1 else f"R{i}") + base) if base in _PAIRED else f"{base}_{i + 1}"
    return [[(i, lab, w) for i, lab, w in r] for r in out]


def layout_pack(rows: list[list[tuple[str, float]]], name: str = "Keyboard") -> dict:
    """The viewer's pack.json for a keyboard whose layout was read from the camera: same part vocabulary, same layers,
    same explode offsets as the hand-built RK68, with the keys where the real keyboard has them."""
    layout = layout_rows_clean(rows)
    width_u = max(sum(w for _, _, w in r) for r in layout)
    width, depth = width_u * U, len(layout) * U
    case_w, case_d = width + 1.12, depth + 0.98
    wall_x, wall_z = (case_w - width) / 2, (case_d - depth) / 2
    nodes: dict[str, list[str]] = {g: [] for g in EXPLODE}
    keys: dict[str, dict] = {}
    for row, r in enumerate(layout):
        x, z = 0.0, row * U + U / 2
        for kid, label, w in r:
            cx, cz = x + w * U / 2 - width / 2, z - depth / 2
            keys[kid] = {"label": label, "width_u": w, "row": row, "x": round(cx, 3), "z": round(cz, 3)}
            nodes["keycaps"].append(f"keycap_{kid}"); nodes["switches"].append(f"switch_{kid}")
            nodes["hotswap_sockets"].append(f"socket_{kid}"); nodes["leds"].append(f"led_{kid}")
            if w >= 2:
                nodes["stabilizers"].append(f"stabilizer_{kid}")
            x += w * U
    for g, n in (("plate", "plate"), ("pcb", "pcb"), ("battery", "battery"), ("case", "case"),
                 ("usb_c_port", "usb_c_port"), ("mode_switch", "mode_switch")):
        nodes[g].append(n)
    back_z = -case_d / 2
    f = max(1.0, width / 30.48)
    space = next((k for k in keys if k == "SPACE"), None)
    return {
        "object": name, "model_file": "", "units": "cm", "layout": f"{sum(len(r) for r in layout)} keys, read from the camera",
        "dimensions_cm": [round(case_w, 2), 3.9, round(case_d, 2)], "key_unit_cm": U,
        "case_cm": [round(case_w, 2), CASE_H, round(case_d, 2)], "key_area_cm": [round(width, 3), round(depth, 3)],
        "wall_cm": [round(wall_x, 3), round(wall_z, 3)], "layers_y": Y,
        "ports": {"usb_c_port": [round(-width / 2 + 2.5, 3), 1.15, round(back_z - 0.1, 3)],
                  "mode_switch": [round(width / 2 - 2.5, 3), 1.15, round(back_z - 0.1, 3)]},
        "groups": nodes, "explode_offsets_cm": EXPLODE, "keys": keys,
        "parts": {"keycap": "keycaps", "mechanical switch": "switches", "switch": "switches", "hot-swap socket": "hotswap_sockets",
                  "socket": "hotswap_sockets", "switch pins": "switches", "plate": "plate", "PCB": "pcb", "stabilizer": "stabilizers",
                  "space bar": "keycap_SPACE" if space else "keycaps", "case": "case", "USB-C port": "usb_c_port", "battery": "battery",
                  "LED": "leds", "backlight": "leds", "mode switch": "mode_switch"},
        "views": {k: [round(c * f, 2) for c in v] for k, v in {"iso": [0, 22, 30], "top": [0, 36, 0.1], "side": [34, 8, 0], "front": [0, 6, 34],
                                                              "back": [0, 10, -32], "detail": [3.5, 8, 10], "socket": [0.5, 6, 4]}.items()},
        "specs": {},
        "layout_id": hashlib.sha1(json.dumps(layout).encode()).hexdigest()[:8],
        "rows": [[lab for _, lab, _ in r] for r in layout],
    }
