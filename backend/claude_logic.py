import base64
import json
import re
import anthropic
import cv2

client = anthropic.Anthropic()
MODEL = "claude-3-5-sonnet-20241022"  # Using a stable, high-performance vision model ID

SYSTEM = """You are an expert hardware repair technician and assistant watching a live camera feed of a keyboard or mouse repair.
You guide users safely and precisely. You understand common hardware issues: mechanical vs. membrane switches, stuck stabilizer bars, sticky keys from liquid spills, ribbon cable connectors, hidden screws under rubber feet, and soldering/hubs.

You receive:
- 1-2 video frames (older first)
- YOLO object detection summary
- Hand movement tracking
- Current repair step the user is performing
- Target issue/goal

YOLO labels can be noisy; rely primarily on visual evidence in the frames.

CRITICAL SAFETY RULES:
1. Always advise unplugging cables or removing batteries before opening electronics.
2. Warn against forcing plastic clips or pulling ribbon cables abruptly.

Respond with ONLY a valid JSON object matching this exact schema:
{
 "hand_on_keyboard": "touching" | "hovering" | "away" | "unclear",
 "fault": {"found": bool, "description": str, "confidence": 0-1},
 "repair_plan": [ {"step": int, "action": str, "tool": str} ],
 "current_step_check": {
    "doing_correct_step": "yes" | "no" | "unclear",
    "movement_ok": "yes" | "no" | "unclear",
    "correction": str
 },
 "say_to_user": str
}

Guidelines for `say_to_user`:
- Must be under 25 words.
- Optimized for Text-to-Speech (TTS): Conversational, warm, direct, and completely free of markdown, bullet symbols, or special characters that sound unnatural when spoken aloud.
- Use "unclear" when frames do not show enough detail."""

def rag


def _img(frame):
  _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
  return {
      "type": "image",
      "source": {
          "type": "base64",
          "media_type": "image/jpeg",
          "data": base64.b64encode(buf).decode(),
      },
  }


def analyze(frames, scene_text, hand_motion, current_step, issue):
  content = [_img(f) for f in frames]
  content.append({
      "type": "text",
      "text": (
          f"{scene_text}\nHand movement: {hand_motion or 'none'}\n"
          f"Current repair step: {current_step or 'none started - diagnose first'}\n"
          f"Target Issue / Goal: {issue or 'none specified'}"
      ),
  })

  try:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=600,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
    )

    raw_text = resp.content[0].text.strip()

    # Safely extract JSON even if model adds minor text or code block tags
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    json_str = match.group(0) if match else raw_text

    return json.loads(json_str)

  except Exception as e:
    # Graceful fallback so your live video loop never crashes on an API or parsing glitch
    print(f"[Repair Assistant Error]: {e}")
    return {
        "hand_on_keyboard": "unclear",
        "fault": {
            "found": False,
            "description": "Error processing frame",
            "confidence": 0.0,
        },
        "repair_plan": [],
        "current_step_check": {
            "doing_correct_step": "unclear",
            "movement_ok": "unclear",
            "correction": "Hold on, let me re-examine the feed.",
        },
        "say_to_user": "Hold on a second, let me check that step again.",
    }