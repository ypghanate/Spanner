# Testing the app

1. Backend in mock mode (no keys): from the repo root, `uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000`
   (`.env` has `USE_MOCKS=1`). Check `http://localhost:8000/health` and watch `http://localhost:8000/debug`.
2. Headless client check: `cd app && npm run smoke` (plays the whole mock repair through the app's own client code).
3. On a phone: `cd app && npx expo start`, scan the QR in Expo Go. `app/.env` must point at the backend:
   same Wi-Fi -> `http://<laptop-ip>:8000`; venue Wi-Fi -> `cloudflared tunnel --url http://localhost:8000`
   and use the https URL. Restart expo after editing `.env`.
4. The mock Pass 2 advances on keyframes: keep the camera pointed at something and the call walks
   identifying -> diagnosing -> researching -> guiding 1..5 -> done -> summary.
