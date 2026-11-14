# fixtures/audio

Needed here (a human has to record or generate these):

- `question.m4a` — a phone recording of someone asking, e.g. "Which size hex key do I need
  for the pad bolt?" Used by the `/audio` fallback route and `scripts/run_pipeline.py`.
- `question.wav` — the same speech as 16 kHz mono PCM16, used by `scripts/live_client.py`
  to stream audio into `WS /session/{id}/live`.

Once an ElevenLabs key is set, `scripts/run_pipeline.py --make-audio` can synthesise
`question.mp3` and `question.wav` with the TTS module so tests have real speech.
