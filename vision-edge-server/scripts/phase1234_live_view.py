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
        description="Display Phase 1-4 detection, tracking, face quality, and identity results."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--window-width", type=int, default=960)
    parser.add_argument("--window-height", type=int, default=540)
    parser.add_argument("--poll-hz", type=float, default=10.0)
    parser.add_argument("--output-dir", default="logs/phase1234_live_view")
    parser.add_argument("--enroll-name", default="")
    parser.add_argument("--external-id", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    base_url = args.base_url.rstrip("/")
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"phase1234_view_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    delay = 1.0 / max(args.poll_hz, 1.0)
    paused = False
    last_frame: np.ndarray | None = None
    last_tracks: dict = {}
    last_faces: dict = {}
    last_identities: dict = {}
    last_fetch_ms = 0.0
    displayed = 0
    started = time.perf_counter()

    window_name = "Smart Office Vision Phase 1-4"
    print("Phase 1-4 live visual test")
    print(f"Server: {base_url}")
    print("Keys: Q/Esc quit, P pause, Space save, R restart, L list identities, E enroll primary")
    if not args.enroll_name:
        print("E is disabled until -EnrollName is supplied to the PowerShell launcher.")
    print(f"JSONL observations: {log_path}")

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, args.window_width, args.window_height)

    with httpx.Client(timeout=8.0, headers={"Cache-Control": "no-cache"}) as client, log_path.open(
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
                    decoded = cv2.imdecode(
                        np.frombuffer(frame_response.content, dtype=np.uint8), cv2.IMREAD_COLOR
                    )
                    if decoded is None:
                        raise RuntimeError("OpenCV could not decode the debug JPEG")
                    tracks_response = client.get(f"{base_url}/api/v1/tracks")
                    faces_response = client.get(f"{base_url}/api/v1/faces")
                    identities_response = client.get(f"{base_url}/api/v1/identities")
                    tracks_response.raise_for_status()
                    faces_response.raise_for_status()
                    identities_response.raise_for_status()
                    last_tracks = tracks_response.json()
                    last_faces = faces_response.json()
                    last_identities = identities_response.json()
                    last_frame = decoded
                    last_fetch_ms = (time.perf_counter() - fetch_started) * 1000.0
                    displayed += 1
                    observation = {
                        "timestamp": datetime.now().isoformat(),
                        "fetch_ms": round(last_fetch_ms, 3),
                        "scene_state": last_tracks.get("scene_state"),
                        "primary_track_id": last_tracks.get("primary_track_id"),
                        "tracks": last_tracks.get("tracks", []),
                        "faces": last_faces.get("faces", []),
                        "recognized_tracks": last_identities.get("recognized_tracks", []),
                        "last_association": last_tracks.get("last_association"),
                    }
                    log_file.write(json.dumps(observation, ensure_ascii=False) + "\n")
                    log_file.flush()
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"

            canvas = compose_canvas(
                last_frame,
                last_tracks,
                last_faces,
                last_identities,
                args.window_width,
                args.window_height,
                last_fetch_ms,
                paused,
                error,
                displayed / max(time.perf_counter() - started, 1e-6),
            )
            cv2.imshow(window_name, canvas)
            key = cv2.waitKey(max(1, int(delay * 1000))) & 0xFF
            if key in (27, ord("q"), ord("Q")):
                break
            if key in (ord("p"), ord("P")):
                paused = not paused
            elif key == 32 and last_frame is not None:
                screenshot = output_dir / f"phase1234_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
                cv2.imwrite(str(screenshot), canvas)
                print(f"Saved screenshot: {screenshot}")
            elif key in (ord("r"), ord("R")):
                try:
                    response = client.post(f"{base_url}/api/v1/vision/restart", timeout=20.0)
                    response.raise_for_status()
                    print("Vision pipeline restart requested.")
                except Exception as exc:
                    print(f"Restart failed: {type(exc).__name__}: {exc}")
            elif key in (ord("l"), ord("L")):
                print(json.dumps(last_identities.get("identities", []), ensure_ascii=False, indent=2))
            elif key in (ord("e"), ord("E")):
                enroll_primary(client, base_url, last_tracks, args.enroll_name, args.external_id)

            elapsed = time.perf_counter() - loop_started
            if elapsed < delay:
                time.sleep(delay - elapsed)

    cv2.destroyAllWindows()
    print(f"Live view stopped. Observations saved to {log_path}")
    return 0


def enroll_primary(
    client: httpx.Client,
    base_url: str,
    tracks: dict,
    display_name: str,
    external_id: str,
) -> None:
    if not display_name:
        print("Enrollment skipped: launch with -EnrollName and press E again.")
        return
    track_id = tracks.get("primary_track_id")
    if not track_id:
        print("Enrollment skipped: there is no primary track.")
        return
    try:
        response = client.post(
            f"{base_url}/api/v1/identities/enroll",
            json={
                "track_id": int(track_id),
                "display_name": display_name,
                "external_id": external_id or None,
                "identity_id": None,
                "consent": True,
                "metadata": {"source": "phase1234_live_view_operator_key"},
            },
            timeout=20.0,
        )
        response.raise_for_status()
        identity = response.json()["identity"]
        print(f"Enrolled {identity['display_name']} as {identity['identity_id']} for track {track_id}.")
    except Exception as exc:
        detail = exc.response.text if isinstance(exc, httpx.HTTPStatusError) else str(exc)
        print(f"Enrollment failed: {type(exc).__name__}: {detail}")


def compose_canvas(
    frame: np.ndarray | None,
    tracks: dict,
    faces: dict,
    identities: dict,
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
            "Waiting for Phase 1-4 debug frame...",
            (25, height // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
    else:
        canvas = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)

    panel_height = 72
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, height - panel_height), (width, height), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.70, canvas, 0.30, 0, canvas)
    recognized = identities.get("recognized_tracks") or []
    primary_id = tracks.get("primary_track_id")
    primary_identity = next(
        (item for item in recognized if item.get("track_id") == primary_id), None
    )
    line1 = (
        f"scene={tracks.get('scene_state', 'unknown')} people={tracks.get('person_count', 0)} "
        f"tracks={tracks.get('track_count', 0)} primary={primary_id} "
        f"name={(primary_identity or {}).get('display_name', 'unknown')}"
    )
    line2 = (
        f"faces={faces.get('face_count', 0)} ready_faces={faces.get('ready_face_count', 0)} "
        f"known={identities.get('identity_count', 0)} recognized={len(recognized)}"
    )
    line3 = f"fetch={fetch_ms:.1f}ms display={display_fps:.1f}fps {'PAUSED' if paused else 'LIVE'}"
    for index, text in enumerate((line1, line2, line3)):
        cv2.putText(
            canvas,
            text,
            (10, height - 50 + index * 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (255, 255, 255) if index < 2 else (220, 220, 220),
            1,
            cv2.LINE_AA,
        )
    if error:
        cv2.putText(
            canvas,
            error[:110],
            (10, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
    return canvas


if __name__ == "__main__":
    raise SystemExit(main())
