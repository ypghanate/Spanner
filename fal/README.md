# fal prototype: "On your keyboard"

Generated with fal on 15 Nov 2026 from a real photo of an RK61. These are the actual outputs, kept at a
repo-friendly size. Nothing here is mocked; it is also **not in the live demo loop** (see Status).

## The idea

Spanner already watches the user's keyboard through the phone camera and knows which key they pulled. The fal
prototype takes that camera frame and asks an image-edit model to show the *current repair step being done on
that exact keyboard*, then an image-to-video model animates the still into a 5-second loop. The user says
"show me on mine" and sees their own board with the puller lifting their key.

## What is in this folder

| File | What it is |
|---|---|
| `01-input-rk61-photo.jpg` | The input photo (an RK61, black, RGB on) |
| `02-input-with-key-ring.jpg` | The same photo with a magenta ring on the J key: the marker the edit model needs to act on the right key |
| `03-nano-banana-edit-keycap-pull.png` | **Chosen model.** Nano Banana edit, 12.6 s: the J keycap lifted by the puller, the switch visible, every other legend intact |
| `04-nano-banana-pro-edit.png` | Nano Banana Pro edit, 17.7 s: same quality, slower |
| `05-flux-kontext-rejected.png` | FLUX Kontext Pro, 6.9 s: fastest but it redrew the board and garbled the legends; rejected |
| `06-kling-2.1-clip-frames.jpg` | **Chosen model.** Kling 2.1 standard image-to-video, 117 s: the hand lifts the keycap, the board stays still |
| `07-hailuo-02-clip-frames.jpg` | Hailuo 02 standard, 156 s: the keycap drifts, the board moves; rejected |
| `08-kling-2.1-keycap-pull.mp4` | The Kling clip, 5 s, re-encoded small |
| `09-trellis-scan-rk61.glb` | Trellis image-to-3D of the same photo (one textured mesh, no separate parts): the "hologram of any device from one photo" idea |

Timings are wall-clock from this laptop, measured in the session.

## What was wired, and why it was taken out

The full feature existed in the app (commit `d0dc745`): a `/coach/visualize` job per (session, step, key) that
took the latest YOLO keyboard crop from `/coach/frame`, ringed the key the coach had located, ran the edit and
the clip, served both from `/data/media`, logged `fal_image` / `fal_video` timings to Databricks, and a viewer
panel that opened on "show me on mine" and closed on "close". It worked end to end on a real frame (image 19 s,
clip 2 min).

It was removed from the demo build the same night (commit `daffe9a`): the clip takes two minutes, the venue
network dropped fal's queue domain for long stretches (status polling fell back to a backup domain and one
batch of 3D scans took 58 minutes of wall time), and the team judged the still alone not worth a panel. The
code is one `git revert` away if a faster network or model makes it worth it.

## Status

Prototype. Real outputs, real wiring in git history, not running in the demo.
