from __future__ import annotations

"""Standalone OpenCV camera test for Smart Office proximity greeting.

This script does not start the Agent, Backend, GPT Realtime, or the browser UI. It
opens a webcam, displays the live frame, detects the largest face, and overlays the
same kinds of measurements used by the proactive-greeting gate:

- face area ratio
- approximate frontal/centering score
- stable qualified-frame count
- final NEAR FRONTAL FACE / NOT QUALIFIED decision

It intentionally performs face *detection*, not identity recognition.

Examples (PowerShell):
    python .\backend\scripts\test_proximity_face_detection.py
    python .\backend\scripts\test_proximity_face_detection.py --camera 1
    python .\backend\scripts\test_proximity_face_detection.py --area-ratio 0.12

Keys:
    q / Esc  quit
    r        reset stable-frame counter
    m        toggle mirror mode
"""

import argparse
import math
import sys
import time
from dataclasses import dataclass

import cv2


@dataclass(frozen=True)
class DetectionConfig:
    camera_index: int
    area_ratio: float
    min_frontal_score: float
    required_stable_frames: int
    width: int
    height: int
    mirror: bool


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def approximate_frontal_score(
    x: int,
    y: int,
    width: int,
    height: int,
    frame_width: int,
    frame_height: int,
) -> tuple[float, float, float]:
    """Estimate frontality from face-box geometry and frame centering.

    Haar detection does not provide eye/nose landmarks. This score therefore
    mirrors only the geometry/centering part of the browser detector. It is a
    diagnostic signal, not biometric recognition.
    """

    aspect = width / max(1.0, float(height))
    aspect_score = clamp(1.0 - abs(aspect - 0.82) / 0.55)
    center_x = (x + width / 2.0) / max(1.0, float(frame_width))
    center_y = (y + height / 2.0) / max(1.0, float(frame_height))
    center_score = clamp(1.0 - abs(center_x - 0.5) / 0.42) * clamp(
        1.0 - abs(center_y - 0.44) / 0.5
    )
    score = clamp(aspect_score * 0.58 + center_score * 0.42)
    return score, center_x, center_y


def parse_args() -> DetectionConfig:
    parser = argparse.ArgumentParser(
        description="Show a webcam preview and test close frontal-face detection."
    )
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index.")
    parser.add_argument(
        "--area-ratio",
        type=float,
        default=0.18,
        help="Minimum face rectangle area divided by full frame area.",
    )
    parser.add_argument(
        "--frontal-score",
        type=float,
        default=0.62,
        help="Minimum approximate frontal/centering score.",
    )
    parser.add_argument(
        "--stable-frames",
        type=int,
        default=4,
        help="Consecutive qualified frames required before trigger state.",
    )
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=540)
    parser.add_argument(
        "--no-mirror",
        action="store_true",
        help="Do not mirror the displayed camera image.",
    )
    args = parser.parse_args()

    if not 0.01 <= args.area_ratio <= 0.9:
        parser.error("--area-ratio must be between 0.01 and 0.9")
    if not 0.0 <= args.frontal_score <= 1.0:
        parser.error("--frontal-score must be between 0 and 1")
    if args.stable_frames < 1:
        parser.error("--stable-frames must be at least 1")

    return DetectionConfig(
        camera_index=args.camera,
        area_ratio=args.area_ratio,
        min_frontal_score=args.frontal_score,
        required_stable_frames=args.stable_frames,
        width=max(320, args.width),
        height=max(240, args.height),
        mirror=not args.no_mirror,
    )


def put_line(
    frame,
    text: str,
    line: int,
    *,
    scale: float = 0.62,
    thickness: int = 2,
) -> None:
    y = 28 + line * 28
    cv2.putText(
        frame,
        text,
        (16, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (0, 0, 0),
        thickness + 3,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
        text,
        (16, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )


def open_camera(config: DetectionConfig) -> cv2.VideoCapture:
    # CAP_DSHOW normally avoids long camera-open delays on Windows. Fall back to
    # the default backend for Linux/macOS or when DirectShow cannot open it.
    if sys.platform.startswith("win"):
        capture = cv2.VideoCapture(config.camera_index, cv2.CAP_DSHOW)
        if capture.isOpened():
            return capture
        capture.release()
    return cv2.VideoCapture(config.camera_index)


def main() -> int:
    config = parse_args()
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(cascade_path)
    if detector.empty():
        print(f"ERROR: Could not load OpenCV face cascade: {cascade_path}")
        return 2

    capture = open_camera(config)
    if not capture.isOpened():
        print(
            f"ERROR: Could not open camera index {config.camera_index}. "
            "Close Teams/Zoom/browser camera tabs or try --camera 1."
        )
        return 3

    capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.height)
    capture.set(cv2.CAP_PROP_FPS, 24)

    window_name = "Smart Office - OpenCV Proximity Face Test"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    stable_frames = 0
    mirror = config.mirror
    last_time = time.perf_counter()
    smoothed_fps = 0.0

    print("OpenCV proximity detector is running.")
    print(f"Camera index: {config.camera_index}")
    print(f"Cascade: {cascade_path}")
    print("Press q or Esc to quit, r to reset, m to toggle mirror.")

    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                print("ERROR: Camera frame read failed.")
                return 4

            if mirror:
                frame = cv2.flip(frame, 1)

            frame_height, frame_width = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.equalizeHist(gray)
            min_face = max(48, int(min(frame_width, frame_height) * 0.08))
            faces = detector.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(min_face, min_face),
                flags=cv2.CASCADE_SCALE_IMAGE,
            )

            qualified = False
            triggered = False
            area_ratio = 0.0
            frontal_score = 0.0
            centered = False

            if len(faces):
                x, y, width, height = max(faces, key=lambda item: int(item[2]) * int(item[3]))
                x, y, width, height = map(int, (x, y, width, height))
                area_ratio = (width * height) / max(1.0, float(frame_width * frame_height))
                frontal_score, center_x, center_y = approximate_frontal_score(
                    x, y, width, height, frame_width, frame_height
                )
                centered = 0.2 <= center_x <= 0.8 and 0.12 <= center_y <= 0.78
                qualified = (
                    area_ratio >= config.area_ratio
                    and frontal_score >= config.min_frontal_score
                    and centered
                )
                stable_frames = stable_frames + 1 if qualified else 0
                triggered = stable_frames >= config.required_stable_frames

                box_color = (0, 220, 0) if triggered else (0, 190, 255) if qualified else (0, 0, 255)
                cv2.rectangle(frame, (x, y), (x + width, y + height), box_color, 3)
                cv2.circle(
                    frame,
                    (int((x + width / 2)), int((y + height / 2))),
                    5,
                    box_color,
                    -1,
                )
            else:
                stable_frames = 0

            now = time.perf_counter()
            instant_fps = 1.0 / max(1e-6, now - last_time)
            smoothed_fps = instant_fps if smoothed_fps == 0 else smoothed_fps * 0.9 + instant_fps * 0.1
            last_time = now

            status = "NEAR FRONTAL FACE - WOULD GREET" if triggered else "QUALIFYING" if qualified else "NOT QUALIFIED"
            put_line(frame, f"OpenCV {cv2.__version__} | camera={config.camera_index} | FPS={smoothed_fps:.1f}", 0)
            put_line(frame, f"Face detected: {'YES' if len(faces) else 'NO'} | largest area={area_ratio:.3f} ({area_ratio * 100:.1f}%)", 1)
            put_line(frame, f"Area threshold: {config.area_ratio:.3f} ({config.area_ratio * 100:.1f}%)", 2)
            put_line(frame, f"Frontal score: {frontal_score:.3f} / {config.min_frontal_score:.3f} | centered={'YES' if centered else 'NO'}", 3)
            put_line(frame, f"Stable frames: {stable_frames}/{config.required_stable_frames}", 4)
            put_line(frame, f"STATUS: {status}", 5, scale=0.72, thickness=2)
            put_line(frame, "Keys: q/Esc quit | r reset | m mirror", 6, scale=0.52, thickness=1)

            cv2.imshow(window_name, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("r"):
                stable_frames = 0
            if key == ord("m"):
                mirror = not mirror
    finally:
        capture.release()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
