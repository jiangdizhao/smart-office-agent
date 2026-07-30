from __future__ import annotations

import argparse
import sys
import time

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the complete detection, tracking, visitor-session and identity pipeline."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--require-face", action="store_true")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    deadline = time.monotonic() + args.timeout_seconds
    last_status = None

    with httpx.Client(timeout=10.0) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(f"{base_url}/api/v1/status")
                response.raise_for_status()
                last_status = response.json()
                vision = last_status.get("vision") or {}
                camera = vision.get("camera") or {}
                detector = vision.get("detector") or {}
                tracking = vision.get("tracking") or {}
                appearance = tracking.get("appearance") or {}
                face = vision.get("face") or {}
                identity = vision.get("identity") or {}
                sessions = vision.get("visitor_sessions") or {}
                face_condition = (
                    int(face.get("recognition_usable_count") or 0) > 0
                    if args.require_face
                    else True
                )
                if (
                    vision.get("status") == "ready"
                    and camera.get("running")
                    and int(camera.get("frame_id") or 0) > 0
                    and detector.get("ready")
                    and "CUDAExecutionProvider" in (detector.get("providers") or [])
                    and int(detector.get("inference_count") or 0) > 0
                    and int(tracking.get("update_count") or 0) > 0
                    and appearance.get("backend") == "osnet_onnx"
                    and face.get("ready")
                    and face.get("model_exists")
                    and int(face.get("detect_count") or 0) > 0
                    and identity.get("ready")
                    and identity.get("model_exists")
                    and sessions.get("ready")
                    and face_condition
                ):
                    break
            except Exception:
                pass
            time.sleep(1.0)
        else:
            print(
                "FAIL: Identity-fusion vision pipeline did not become ready before timeout.",
                file=sys.stderr,
            )
            print(last_status, file=sys.stderr)
            return 1

        tracks_response = client.get(f"{base_url}/api/v1/tracks")
        faces_response = client.get(f"{base_url}/api/v1/faces")
        identities_response = client.get(f"{base_url}/api/v1/identities")
        frame_response = client.get(f"{base_url}/api/v1/debug/frame.jpg")
        for response in (
            tracks_response,
            faces_response,
            identities_response,
            frame_response,
        ):
            response.raise_for_status()
        if frame_response.headers.get("content-type", "").split(";")[0] != "image/jpeg":
            print("FAIL: debug frame is not image/jpeg", file=sys.stderr)
            return 1
        if len(frame_response.content) < 1000:
            print("FAIL: debug frame is unexpectedly small", file=sys.stderr)
            return 1

    vision = last_status["vision"]
    tracks = tracks_response.json()
    faces = faces_response.json()
    identities = identities_response.json()
    sessions = tracks.get("visitor_sessions") or {}
    print("PASS: Detection, tracking, visitor-session and face identity pipeline is ready.")
    print(f"Camera: {vision['camera']['actual']}")
    print(f"Detector providers: {vision['detector']['providers']}")
    print(f"Detector inference count: {vision['detector']['inference_count']}")
    print(f"Appearance backend: {(tracks.get('appearance') or {}).get('backend')}")
    print(f"Tracking updates: {tracks.get('update_count')}")
    print(f"Active tracks: {tracks.get('track_count')}")
    print(
        "Face detections: "
        f"{faces.get('face_count')} "
        f"(recognition_usable={faces.get('recognition_usable_count')}, "
        f"enrollment_usable={faces.get('enrollment_usable_count')})"
    )
    print(f"Face cycles: {faces.get('detect_count')}")
    print(f"Known people: {identities.get('identity_count')}")
    print(f"Database identity rows: {identities.get('database_identity_row_count')}")
    print(f"Recognized tracks: {len(identities.get('recognized_tracks') or [])}")
    print(
        f"Visitor sessions: {sessions.get('session_count')} "
        f"(recovered={sessions.get('recovery_count')})"
    )
    print(f"Latest timings: {vision.get('last_timings')}")
    print(f"Debug JPEG bytes: {len(frame_response.content)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
