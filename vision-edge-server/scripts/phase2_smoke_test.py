from __future__ import annotations

import argparse
import sys
import time

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the live Phase 1 + Phase 2 pipeline.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--require-osnet",
        action="store_true",
        help="Fail unless the neural OSNet ONNX appearance backend is active.",
    )
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
                appearance_ok = appearance.get("ready") and (
                    not args.require_osnet or appearance.get("backend") == "osnet_onnx"
                )
                if (
                    vision.get("status") == "ready"
                    and camera.get("running")
                    and int(camera.get("frame_id") or 0) > 0
                    and detector.get("ready")
                    and int(detector.get("inference_count") or 0) > 0
                    and int(tracking.get("update_count") or 0) > 0
                    and appearance_ok
                ):
                    break
            except Exception:
                pass
            time.sleep(1.0)
        else:
            requirement = " with OSNet" if args.require_osnet else ""
            print(
                f"FAIL: Phase 1 + Phase 2{requirement} did not become ready before timeout.",
                file=sys.stderr,
            )
            print(last_status, file=sys.stderr)
            return 1

        tracks_response = client.get(f"{base_url}/api/v1/tracks")
        tracks_response.raise_for_status()
        frame_response = client.get(f"{base_url}/api/v1/debug/frame.jpg")
        frame_response.raise_for_status()
        if frame_response.headers.get("content-type", "").split(";")[0] != "image/jpeg":
            print("FAIL: debug frame is not image/jpeg", file=sys.stderr)
            return 1
        if len(frame_response.content) < 1000:
            print("FAIL: debug frame is unexpectedly small", file=sys.stderr)
            return 1

    vision = last_status["vision"]
    tracking = tracks_response.json()
    print("PASS: Phase 1 detection and Phase 2 tracking pipeline is ready.")
    print(f"Camera frame: {vision['camera']['frame_id']}")
    print(f"Camera actual: {vision['camera']['actual']}")
    print(f"Detector providers: {vision['detector']['providers']}")
    print(f"Detector inference count: {vision['detector']['inference_count']}")
    print(f"Tracking updates: {tracking.get('update_count')}")
    print(f"Appearance backend: {(tracking.get('appearance') or {}).get('backend')}")
    print(f"Visible people: {tracking.get('person_count')}")
    print(f"Active tracks: {tracking.get('track_count')}")
    print(f"Primary track: {tracking.get('primary_track_id')}")
    print(f"Last association: {tracking.get('last_association')}")
    print(f"Latest timings: {vision.get('last_timings')}")
    print(f"Debug JPEG bytes: {len(frame_response.content)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
