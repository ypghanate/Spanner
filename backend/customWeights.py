from collections import defaultdict
import os
import cv2
import mediapipe as mp
import numpy as np
from inference_sdk import InferenceHTTPClient

# ---------------------------------------------------------------- CONFIG
CONF_THRESHOLD = 0.35
MOVEMENT_THRESHOLD = 20  # Filter out micro-jitters; only large movements trigger
FONT_SCALE = 0.6
FONT_THICKNESS = 2
WINDOW = "Mechanic AI - Roboflow Custom Model + Hand Vectors"

# 1. Initialize Roboflow Inference Client
CLIENT = InferenceHTTPClient(
    api_url="https://detect.roboflow.com",
    api_key=os.environ.get("ROBOFLOW_API_KEY"),
)
MODEL_ID = "yash-ghanate/repair-eqfnk-1-rfdetr-nas-pecoret-t1--01f35b"

# 2. Initialize MediaPipe Hands for Movement Vectors
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=2,
    min_detection_confidence=0.7,
    min_tracking_confidence=0.7,
)
mp_draw = mp.solutions.drawing_utils

prev_hand_positions = {}

# ---------------------------------------------------------------- MAIN
cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)

if not cap.isOpened():
  print(
      "Error: Camera failed to initialize. Please check System Settings >"
      " Privacy & Security > Camera."
  )
else:
  print("Camera open. Mechanic AI with Roboflow Custom Model running...")
  print("Press 'q' in the video window to exit.")

  while cap.isOpened():
    success, frame = cap.read()
    if not success:
      print("Failed to grab frame. Exiting...")
      break

    h_frame, w_frame, _ = frame.shape

    # --- A. Run Roboflow Custom Model Inference on Frame ---
    try:
      # Pass the live OpenCV numpy frame directly to Roboflow API
      response = CLIENT.infer(frame, model_id=MODEL_ID)
      predictions = response.get("predictions", [])
    except Exception as e:
      print(f"Inference error: {e}")
      predictions = []

    annotated = frame.copy()

    # Draw Roboflow predictions manually using OpenCV
    for pred in predictions:
      conf = pred.get("confidence", 0.0)
      if conf >= CONF_THRESHOLD:
        x = int(pred["x"])
        y = int(pred["y"])
        w = int(pred["width"])
        h = int(pred["height"])
        class_name = pred.get("class", "object")

        # Convert center coordinates (x, y) to top-left corner box coordinates
        xmin = int(x - w / 2)
        ymin = int(y - h / 2)
        xmax = int(x + w / 2)
        ymax = int(y + h / 2)

        # Draw bounding box
        cv2.rectangle(
            annotated, (xmin, ymin), (xmax, ymax), (0, 255, 0), FONT_THICKNESS
        )

        # Draw label background & text
        label = f"{class_name}: {conf:.2f}"
        (text_width, text_height), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, FONT_SCALE, 1
        )
        cv2.rectangle(
            annotated,
            (xmin, ymin - text_height - baseline - 5),
            (xmin + text_width, ymin),
            (0, 255, 0),
            -1,
        )
        cv2.putText(
            annotated,
            label,
            (xmin, ymin - baseline - 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            FONT_SCALE,
            (0, 0, 0),
            1,
        )

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

        # --- C. Vector & Directional Calculation ---
        if hand_idx in prev_hand_positions:
          prev_x, prev_y = prev_hand_positions[hand_idx]

          dx = curr_x - prev_x
          dy = curr_y - prev_y

          direction_x = ""
          direction_y = ""

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
                0.6,
                (0, 255, 0),
                2,
            )
            cv2.putText(
                annotated,
                dir_text,
                (curr_x + 20, curr_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 255),
                2,
            )

        prev_hand_positions[hand_idx] = (curr_x, curr_y)

    cv2.imshow(WINDOW, annotated)

    if cv2.waitKey(1) & 0xFF == ord("q"):
      break

  cap.release()
  cv2.destroyAllWindows()