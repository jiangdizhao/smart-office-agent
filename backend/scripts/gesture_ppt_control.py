import time
from collections import deque
from dataclasses import dataclass
from typing import Optional
import cv2
import mediapipe as mp



@dataclass
class SwipeConfig:
    camera_index: int = 0
    mirror: bool = True

    max_buffer_seconds: float = 0.55
    min_motion_seconds: float = 0.04
    min_points: int = 3

    min_dx: float = 0.07         # normalized image width
    max_abs_dy: float = 0.35      # normalized image height
    min_abs_speed_x: float = 0.10 # normalized width per second

    cooldown_seconds: float = 0.7

    min_detection_confidence: float = 0.55
    min_tracking_confidence: float = 0.55


class SwipeDetector:
    def __init__(self, cfg: SwipeConfig):
        self.cfg = cfg
        self.points = deque()
        self.last_trigger_time = 0.0

    def update(self, t: float, x: float, y: float) -> Optional[str]:
        self.points.append((t, x, y))

        # Remove old points.
        while self.points and (t - self.points[0][0]) > self.cfg.max_buffer_seconds:
            self.points.popleft()

        if len(self.points) < self.cfg.min_points:
            return None

        if (t - self.last_trigger_time) < self.cfg.cooldown_seconds:
            return None

        t0, x0, y0 = self.points[0]
        t1, x1, y1 = self.points[-1]

        dt = max(t1 - t0, 1e-6)
        if dt < self.cfg.min_motion_seconds:
            return None

        dx = x1 - x0
        dy = y1 - y0
        speed_x = dx / dt

        if abs(dy) > self.cfg.max_abs_dy:
            return None

        if dx > self.cfg.min_dx and speed_x > self.cfg.min_abs_speed_x:
            self.last_trigger_time = t
            self.points.clear()
            return "SWIPE_RIGHT"

        if dx < -self.cfg.min_dx and speed_x < -self.cfg.min_abs_speed_x:
            self.last_trigger_time = t
            self.points.clear()
            return "SWIPE_LEFT"

        return None


def get_palm_center(hand_landmarks) -> tuple[float, float]:
    """
    Return normalized palm center using stable palm-side landmarks.
    MediaPipe landmark indices:
    0 = wrist, 5 = index MCP, 9 = middle MCP, 13 = ring MCP, 17 = pinky MCP.
    """
    ids = [0, 5, 9, 13, 17]
    xs = [hand_landmarks.landmark[i].x for i in ids]
    ys = [hand_landmarks.landmark[i].y for i in ids]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def main() -> None:
    cfg = SwipeConfig()

    cap = cv2.VideoCapture(cfg.camera_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise RuntimeError(
            f"Cannot open webcam index {cfg.camera_index}. "
            "Try camera_index=1 or close other apps using the camera."
        )

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    mp_hands = mp.solutions.hands
    mp_drawing = mp.solutions.drawing_utils
    detector = SwipeDetector(cfg)

    last_event = "None"

    with mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=1,
        model_complexity=0,
        min_detection_confidence=cfg.min_detection_confidence,
        min_tracking_confidence=cfg.min_tracking_confidence,
    ) as hands:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Failed to read frame.")
                break

            if cfg.mirror:
                frame = cv2.flip(frame, 1)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            results = hands.process(rgb)
            rgb.flags.writeable = True

            h, w = frame.shape[:2]
            now = time.time()

            if results.multi_hand_landmarks:
                hand_landmarks = results.multi_hand_landmarks[0]
                palm_x, palm_y = get_palm_center(hand_landmarks)

                event = detector.update(now, palm_x, palm_y)
                if event:
                    last_event = event
                    print(event)

                # Draw landmarks.
                mp_drawing.draw_landmarks(
                    frame,
                    hand_landmarks,
                    mp_hands.HAND_CONNECTIONS,
                )

                # Draw palm center.
                cx = int(palm_x * w)
                cy = int(palm_y * h)
                cv2.circle(frame, (cx, cy), 10, (0, 255, 255), -1)

                cv2.putText(
                    frame,
                    f"Palm: x={palm_x:.2f}, y={palm_y:.2f}",
                    (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 255, 255),
                    2,
                )
            else:
                detector.points.clear()

            cv2.putText(
                frame,
                f"Last Event: {last_event}",
                (20, 80),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 255, 0),
                2,
            )

            cv2.putText(
                frame,
                "Swipe right = next | Swipe left = previous | q = quit",
                (20, h - 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
            )

            cv2.imshow("Smart Office Gesture Control", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()