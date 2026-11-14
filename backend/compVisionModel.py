from collections import defaultdict
from pathlib import Path

import cv2
from ultralytics import YOLO

# 1. LOAD MODEL
model = YOLO(Path(__file__).with_name('yolov8n.pt'))

# Store tracking history to calculate movement direction
track_history = defaultdict(lambda: [])

# 2. OPEN CAMERA STREAM WITH AVFOUNDATION BACKEND
# (Explicitly pointing to index 0 using macOS AVFoundation)
cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)

if not cap.isOpened():
  print(
      'Error: Camera failed to initialize. Please check System Settings >'
      ' Privacy & Security > Camera.'
  )
else:
  print('Success! Camera is open. Starting Mechanic AI Vision Stream...')
  print("Press 'q' in the video window to exit.")

  while cap.isOpened():
    success, frame = cap.read()
    if not success:
      print('Failed to grab frame. Exiting...')
      break

    # 3. RUN TRACKING (persist=True maintains consistent IDs across frames)
    results = model.track(
        frame, persist=True, tracker='bytetrack.yaml', verbose=False
    )

    # Check if any objects were detected in this frame
    if results[0].boxes.id is not None:
      boxes = results[0].boxes.xyxy.cpu()  # Bounding box coordinates [x1, y1, x2, y2]
      track_ids = (
          results[0].boxes.id.int().cpu().tolist()
      )  # Persistent Object IDs
      confidences = results[0].boxes.conf.cpu().tolist()  # Confidence scores
      class_ids = results[0].boxes.cls.int().cpu().tolist()  # Class indices

      # Loop through each detected object
      for box, track_id, conf, cls_id in zip(
          boxes, track_ids, confidences, class_ids
      ):
        x1, y1, x2, y2 = box
        class_name = model.names[cls_id]

        # 4. CALCULATE CENTER POINT (for movement direction)
        center_x = int((x1 + x2) / 2)
        center_y = int((y1 + y2) / 2)

        # Append current position to this specific track ID's history
        history = track_history[track_id]
        history.append((center_x, center_y))
        if len(history) > 30:  # Keep last 30 frames of movement
          history.pop(0)

        # 5. TRACK DIRECTION OF MOVEMENT
        direction_text = 'Stationary'
        if len(history) >= 2:
          dx = history[-1][0] - history[-2][0]  # Change in X
          dy = history[-1][1] - history[-2][1]  # Change in Y

          dir_x = 'Right' if dx > 3 else ('Left' if dx < -3 else '')
          dir_y = 'Down' if dy > 3 else ('Up' if dy < -3 else '')
          direction_text = f'{dir_y} {dir_x}'.strip() or 'Stationary'

        # 6. CUSTOM "BROKEN vs. INTACT" LOGIC MOCKUP
        status = 'Unknown'
        if 'broken' in class_name.lower():
          status = '🚨 BROKEN / DEFECTIVE'
        elif 'intact' in class_name.lower() or 'good' in class_name.lower():
          status = '✅ Intact'

        # Print diagnostic info to console
        print(
            f'ID: {track_id} | Object: {class_name} | Conf: {conf:.2f} |'
            f' Direction: {direction_text} | Status: {status}'
        )

        # Draw custom text info on the frame
        cv2.putText(
            frame,
            f'ID {track_id}: {class_name} ({conf:.2f})',
            (int(x1), int(y1 - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            2,
        )

    # Automatically render built-in YOLO bounding boxes and labels
    annotated_frame = results[0].plot()

    # Display live visual feedback
    cv2.imshow('Mechanic AI - Live Feed', annotated_frame)

    # Press 'q' to exit the live window
    if cv2.waitKey(1) & 0xFF == ord('q'):
      break

  cap.release()
  cv2.destroyAllWindows()