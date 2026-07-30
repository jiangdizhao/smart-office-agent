from __future__ import annotations

import argparse
import json
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage consent-based local face identities.")
    parser.add_argument("action", choices=["list", "enroll", "delete"])
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--track-id", type=int)
    parser.add_argument("--display-name")
    parser.add_argument("--external-id")
    parser.add_argument("--identity-id")
    parser.add_argument("--consent", action="store_true")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    try:
        with httpx.Client(timeout=20.0) as client:
            if args.action == "list":
                response = client.get(f"{base_url}/api/v1/identities")
            elif args.action == "enroll":
                if not args.track_id or not args.display_name:
                    parser.error("enroll requires --track-id and --display-name")
                if not args.consent:
                    parser.error("enroll requires --consent")
                response = client.post(
                    f"{base_url}/api/v1/identities/enroll",
                    json={
                        "track_id": args.track_id,
                        "display_name": args.display_name,
                        "external_id": args.external_id,
                        "identity_id": args.identity_id,
                        "consent": True,
                        "metadata": {"source": "local_operator_cli"},
                    },
                )
            else:
                if not args.identity_id:
                    parser.error("delete requires --identity-id")
                response = client.delete(f"{base_url}/api/v1/identities/{args.identity_id}")
            response.raise_for_status()
            print(json.dumps(response.json(), ensure_ascii=False, indent=2))
            return 0
    except httpx.HTTPStatusError as exc:
        print(f"FAIL: HTTP {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
