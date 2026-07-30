from __future__ import annotations

import argparse
import sys
import time

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Phase 3 face quality and Phase 4 identity services.")
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
                face = vision.get("face") or {}
                identity = vision.get("identity") or {}
                face_condition = int(face.get("ready_face_count") or 0) > 0 if args.require_face else True
                if (
                    vision.get("status") == "ready"
                    and face.get("ready")
                    and face.get("model_exists")
                    and identity.get("ready")
                    and identity.get("model_exists")
                    and face_condition
                ):
                    break
            except Exception:
                pass
            time.sleep(1.0)
        else:
            print("FAIL: Phase 3 + Phase 4 did not become ready before timeout.", file=sys.stderr)
            print(last_status, file=sys.stderr)
            return 1

        faces = client.get(f"{base_url}/api/v1/faces")
        faces.raise_for_status()
        identities = client.get(f"{base_url}/api/v1/identities")
        identities.raise_for_status()
        tracks = client.get(f"{base_url}/api/v1/tracks")
        tracks.raise_for_status()
        frame = client.get(f"{base_url}/api/v1/debug/frame.jpg")
        frame.raise_for_status()
        if frame.headers.get("content-type", "").split(";")[0] != "image/jpeg":
            print("FAIL: debug frame is not image/jpeg", file=sys.stderr)
            return 1
        if len(frame.content) < 1000:
            print("FAIL: debug frame is unexpectedly small", file=sys.stderr)
            return 1

    face_data = faces.json()
    identity_data = identities.json()
    track_data = tracks.json()
    print("PASS: Phase 3 face analysis and Phase 4 local identity runtime are ready.")
    print(f"Face model: {face_data.get('model_path')}")
    print(f"Face detections: {face_data.get('face_count')} (ready={face_data.get('ready_face_count')})")
    print(f"Face inference count: {face_data.get('detect_count')}")
    print(f"Identity model: {identity_data.get('model_path')}")
    print(f"Known identities: {identity_data.get('identity_count')}")
    print(f"Recognized tracks: {len(identity_data.get('recognized_tracks') or [])}")
    print(f"Active tracks: {track_data.get('track_count')}")
    print(f"Debug JPEG bytes: {len(frame.content)}")
    print(f"Privacy: {identity_data.get('privacy')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
