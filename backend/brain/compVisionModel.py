from collections import defaultdict
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from ultralytics import YOLO

# ---------------------------------------------------------------- CONFIG
CONF_THRESHOLD = 0.35        # General confidence threshold
PERIPHERAL_CONF_THRESHOLD = 0.02  # Extremely low threshold: if there's even a small chance, keep it!
PHONE_CONF_THRESHOLD = 0.65  # Higher threshold specifically for phones
MOVEMENT_THRESHOLD = 20      # Filter out micro-jitters; only large movements trigger
FONT_SCALE = 1.0             # Large, highly readable text scale
FONT_THICKNESS = 2           # Thicker font lines
WINDOW = "Mechanic AI - YOLO-World + Hand Vectors"

# NEW: mouse/keyboard protection
PERIPHERAL_KEYWORDS = ("mouse", "keyboard")
PROTECTED_CLASSES = ("person", "face", "hand", "fingers")  # never suppressed
OVERLAP_THRESHOLD = 0.5      # overlap needed to treat two boxes as the same object

# 1. Load YOLO-World Open-Vocabulary Model locally (High Speed)
model = YOLO("yolov8s-worldv2.pt")

# 2. Reverted Normal Classes List
custom_classes = [
    # People & Face
    "person",
    "face",
    "human face",
    "hand",
    "fingers",

    # Peripherals & Electronics (Normal)
    "computer mouse",
    "wireless mouse",
    "gaming mouse",
    "keyboard",
    "mechanical keyboard",
    "cell phone",
    "smartphone",
    "mobile phone",
    "laptop",
    "desktop computer",
    "monitor",
    "display screen",
    "tablet",
    "charging cable",
    "USB cable",
    "power strip",
    "adapter",
    "headset",
    "headphones",
    

    # Repair Tools
    "screwdriver",
    "pliers",
    "wrench",
    "soldering iron",
    "tweezers",
    "multimeter",
    "screw",
    "nut",
    "bolt",
    "circuit board",
    "soldering wire",
    "repair mat",
    "utility knife",

    # Office & Desk Objects
    "coffee mug",
    "water bottle",
    "pen",
    "pencil",
    "notebook",
    "book",
    "scissors",
    "stapler",
    "desk lamp",
    "glasses",
    "backpack",
    "keys",
    "wallet",
]

model.set_classes(custom_classes)
track_history = defaultdict(lambda: [])

# 3. Initialize MediaPipe Hands for Movement Vectors
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=2,
    min_detection_confidence=0.7,
    min_tracking_confidence=0.7,
)
mp_draw = mp.solutions.drawing_utils

# Store previous hand positions for vector calculations
prev_hand_positions = {}


# ---------------------------------------------------------------- HELPERS (NEW)
def iou(a, b):
  ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
  ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
  inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
  union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
  return inter / union if union > 0 else 0.0


def covered_fraction(a, b):
  """Fraction of box a that lies inside box b."""
  ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
  ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
  inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
  area = (a[2] - a[0]) * (a[3] - a[1])
  return inter / area if area > 0 else 0.0


# ---------------------------------------------------------------- MAIN
cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)

if not cap.isOpened():
  print(
      "Error: Camera failed to initialize. Please check System Settings >"
      " Privacy & Security > Camera."
  )
else:
  print("Camera open. Mechanic AI YOLO-World running at full speed...")
  print("Press 'q' in the video window to exit.")

  while cap.isOpened():
    success, frame = cap.read()
    if not success:
      print("Failed to grab frame. Exiting...")
      break

    h_frame, w_frame, _ = frame.shape

    # --- A. Run Local YOLO-World Object Detection ---
    results = model.predict(
        frame, conf=PERIPHERAL_CONF_THRESHOLD, max_det=30, verbose=False
    )

    if results[0].boxes is not None and len(results[0].boxes) > 0:
      boxes = results[0].boxes
      xyxy = boxes.xyxy.tolist()
      confs = boxes.conf.tolist()
      names = [model.names[int(c)].lower() for c in boxes.cls.tolist()]
      is_periph = [any(k in n for k in PERIPHERAL_KEYWORDS) for n in names]

      # Pass 1: thresholds. Mouse/keyboard are ALWAYS kept, even at low confidence.
      keep = []
      for i in range(len(names)):
        if is_periph[i]:
          keep.append(i)
          continue
        if "phone" in names[i] and confs[i] < PHONE_CONF_THRESHOLD:
          continue
        if confs[i] >= CONF_THRESHOLD:
          keep.append(i)

      # Pass 2: several mouse/keyboard labels on one object -> keep the most confident
      periph_sorted = sorted(
          [i for i in keep if is_periph[i]], key=lambda i: -confs[i]
      )
      winners = []
      for i in periph_sorted:
        if all(iou(xyxy[i], xyxy[j]) < OVERLAP_THRESHOLD for j in winners):
          winners.append(i)

      # Pass 3: other classes (phone, remote, etc.) cannot relabel an object
      # already called a mouse/keyboard. People/hands are never suppressed.
      final = list(winners)
      for i in keep:
        if is_periph[i]:
          continue
        if any(p in names[i] for p in PROTECTED_CLASSES):
          final.append(i)
          continue
        if all(covered_fraction(xyxy[i], xyxy[j]) < OVERLAP_THRESHOLD
               for j in winners):
          final.append(i)

      final.sort()
      results[0].boxes = boxes[final] if final else None

    annotated = results[0].plot(img=frame) if results[0].boxes is not None else frame

    # --- B. Run MediaPipe Hand & Finger Vector Tracking ---
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_results = hands.process(rgb_frame)

    if mp_results.multi_hand_landmarks:
      for hand_idx, hand_landmarks in enumerate(mp_results.multi_hand_landmarks):
        mp_draw.draw_landmarks(
            annotated, hand_landmarks, mp_hands.HAND_CONNECTIONS
        )

        target_landmark = hand_landmarks.landmark[
            mp_hands.HandLandmark.INDEX_FINGER_TIP
        ]
        curr_x = int(target_landmark.x * w_frame)
        curr_y = int(target_landmark.y * h_frame)

        # --- C. Vector & Directional Calculation (Swapped Left/Right X-axis) ---
        if hand_idx in prev_hand_positions:
          prev_x, prev_y = prev_hand_positions[hand_idx]

          dx = curr_x - prev_x
          dy = curr_y - prev_y  # Screen Y goes down (+Y)

          direction_x = ""
          direction_y = ""

          # Swapped X semantics: Left = +X, Right = -X
          if abs(dx) > MOVEMENT_THRESHOLD:
            direction_x = "Left (+X)" if dx > 0 else "Right (-X)"

          if abs(dy) > MOVEMENT_THRESHOLD:
            direction_y = "Down (+Y)" if dy > 0 else "Up (-Y)"

          if direction_x or direction_y:
            vector_text = f"Hand {hand_idx} V:({dx}, {-dy})"
            dir_text = f"Move: {direction_x} {direction_y}"

            cv2.putText(
                annotated,
                vector_text,
                (curr_x + 20, curr_y - 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                FONT_SCALE,
                (0, 255, 0),
                FONT_THICKNESS,
            )
            cv2.putText(
                annotated,
                dir_text,
                (curr_x + 20, curr_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                FONT_SCALE,
                (0, 255, 255),
                FONT_THICKNESS,
            )

        prev_hand_positions[hand_idx] = (curr_x, curr_y)

    cv2.imshow(WINDOW, annotated)

    if cv2.waitKey(1) & 0xFF == ord("q"):
      break

  cap.release()
  cv2.destroyAllWindows()