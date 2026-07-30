from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import cv2
import httpx
import numpy as np


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Display the Phase 1 detector and Phase 2 tracker in a compact OpenCV window."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--window-width", type=int, default=960)
    parser.add_argument("--window-height", type=int, default=540)
    parser.add_argument("--poll-hz", type=float, default=10.0)
    parser.add_argument("--output-dir", default="logs/phase12_live_view")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    base_url = args.base_url.rstrip("/")
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"phase12_view_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    delay = 1.0 / max(args.poll_hz, 1.0)
    paused = False
    last_frame: np.ndarray | None = None
    last_tracks: dict = {}
    last_fetch_ms = 0.0
    displayed = 0
    started = time.perf_counter()

    print("Phase 1 + Phase 2 live visual test")
    print(f"Server: {base_url}")
    print("Keys: Q/Esc quit, P pause, Space save screenshot, R restart pipeline")
    print(f"JSONL observations: {log_path}")

    cv2.namedWindow("Smart Office Vision Phase 1 + 2", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Smart Office Vision Phase 1 + 2", args.window_width, args.window_height)

    with httpx.Client(timeout=5.0, headers={"Cache-Control": "no-cache"}) as client, log_path.open(
        "a", encoding="utf-8"
    ) as log_file:
        while True:
            loop_started = time.perf_counter()
            error: str | None = None
            if not paused:
                try:
                    fetch_started = time.perf_counter()
                    frame_response = client.get(f"{base_url}/api/v1/debug/frame.jpg")
                    frame_response.raise_for_status()
                    encoded = np.frombuffer(frame_response.content, dtype=np.uint8)
                    decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
                    if decoded is None:
                        raise RuntimeError("OpenCV could not decode the debug JPEG")
                    tracks_response = client.get(f"{base_url}/api/v1/tracks")
                    tracks_response.raise_for_status()
                    last_tracks = tracks_response.json()
                    last_frame = decoded
                    last_fetch_ms = (time.perf_counter() - fetch_started) * 1000.0
                    displayed += 1
                    observation = {
                        "timestamp": datetime.now().isoformat(),
                        "fetch_ms": round(last_fetch_ms, 3),
                        "scene_state": last_tracks.get("scene_state"),
                        "person_count": last_tracks.get("person_count"),
                        "track_count": last_tracks.get("track_count"),
                        "primary_track_id": last_tracks.get("primary_track_id"),
                        "last_association": last_tracks.get("last_association"),
                        "tracks": last_tracks.get("tracks", []),
                    }
                    log_file.write(json.dumps(observation, ensure_ascii=False) + "\n")
                    log_file.flush()
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"

            canvas = _compose_canvas(
                last_frame,
                last_tracks,
                args.window_width,
                args.window_height,
                last_fetch_ms,
                paused,
                error,
                displayed / max(time.perf_counter() - started, 1e-6),
            )
            cv2.imshow("Smart Office Vision Phase 1 + 2", canvas)
            key = cv2.waitKey(max(1, int(delay * 1000))) & 0xFF
            if key in (27, ord("q"), ord("Q")):
                break
            if key in (ord("p"), ord("P")):
                paused = not paused
            elif key == 32 and last_frame is not None:
                screenshot = output_dir / f"phase12_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
                cv2.imwrite(str(screenshot), canvas)
                print(f"Saved screenshot: {screenshot}")
            elif key in (ord("r"), ord("R")):
                try:
                    response = client.post(f"{base_url}/api/v1/vision/restart", timeout=15.0)
                    response.raise_for_status()
                    print("Vision pipeline restart requested.")
                except Exception as exc:
                    print(f"Restart failed: {type(exc).__name__}: {exc}")

            elapsed = time.perf_counter() - loop_started
            if elapsed < delay:
                time.sleep(delay - elapsed)

    cv2.destroyAllWindows()
    print(f"Live view stopped. Observations saved to {log_path}")
    return 0


def _compose_canvas(
    frame: np.ndarray | None,
    tracks: dict,
    width: int,
    height: int,
    fetch_ms: float,
    paused: bool,
    error: str | None,
    display_fps: float,
) -> np.ndarray:
    if frame is None:
        canvas = np.zeros((height, width, 3), dtype=np.uint8)
        cv2.putText(
            canvas,
            "Waiting for Phase 1 + 2 debug frame...",
            (25, height // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
    else:
        canvas = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)

    panel_height = 54
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, height - panel_height), (width, height), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.68, canvas, 0.32, 0, canvas)
    appearance = (tracks.get("appearance") or {}).get("backend", "unknown")
    line1 = (
        f"scene={tracks.get('scene_state', 'unknown')}  people={tracks.get('person_count', 0)}  "
        f"tracks={tracks.get('track_count', 0)}  primary={tracks.get('primary_track_id')}"
    )
    line2 = (
        f"appearance={appearance}  fetch={fetch_ms:.1f}ms  display={display_fps:.1f}fps  "
        f"{'PAUSED' if paused else 'LIVE'}"
    )
    cv2.putText(canvas, line1, (10, height - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(canvas, line2, (10, height - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (220, 220, 220), 1, cv2.LINE_AA)
    if error:
        cv2.putText(canvas, error[:110], (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 0, 255), 2, cv2.LINE_AA)
    return canvas


if __name__ == "__main__":
    raise SystemExit(main())
