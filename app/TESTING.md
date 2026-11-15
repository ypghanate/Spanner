# Testing the app

The phone is the laptop webapp's camera: it pairs by scanning the QR the laptop viewer shows, uploads photos to the
laptop's `/coach/*` API, and shows the live coach data (step n/11, mistakes, spoken line, keyboard box). The laptop
does the listening, speaking and the hologram. The app no longer needs `EXPO_PUBLIC_BACKEND_URL` for a call: the
address comes from the QR.

1. Laptop: `uv run python scripts/serve.py` (https, LAN) or `uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000`.
   `GET /coach/health` must answer (an older server without the coach routes will not).
2. **Use an address the phone trusts.** Expo Go rejects the self-signed certificate that `serve.py` makes, so open the
   laptop viewer through the tunnel: `cloudflared tunnel --url http://localhost:8000` and browse to
   `<tunnel-url>/training/viewer/?phone=1`. (Same Wi-Fi works too with plain `http://<laptop-ip>:8000`.)
   The phone must reach the **same server process** the laptop viewer is talking to.
3. Laptop viewer: click to wake it and say "use my phone" (or open it with `?phone=1`). It shows the QR.
4. Phone: `cd app && npx expo start`, open in Expo Go, Home -> **Pair with laptop**, scan the QR.
   The laptop's "waiting for the first frame" clears; the hologram follows the keyboard's rotation
   (say "calibrate" on the laptop if it is turned; `?flip=1` if it turns the wrong way).
5. Headless check of the laptop client: backend up, then `cd app && npm run coach-smoke -- http://127.0.0.1:8000`.
   (`npm run smoke` still exercises the old `/session` client in `src/lib/live.ts`, which the UI no longer uses.)

`/coach/frame` (which step you are on) needs `ANTHROPIC_API_KEY` on the laptop; `/coach/pose` needs the YOLO detector.
