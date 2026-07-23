from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.office_artifacts import (  # noqa: E402
    GMAIL_COMPOSE_SCOPE,
    gmail_credentials_path,
    gmail_token_path,
)


def main() -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise SystemExit(
            "Google OAuth dependencies are missing. Run: "
            "pip install -r backend/requirements-smartoffice.txt"
        ) from exc

    credentials_path = gmail_credentials_path()
    token_path = gmail_token_path()
    if not credentials_path.is_file():
        raise SystemExit(
            "Gmail OAuth desktop credentials were not found at: "
            f"{credentials_path}\n"
            "Create a Google Cloud OAuth 2.0 Desktop application, download its JSON, "
            "and save it at that path or set SMART_OFFICE_GMAIL_CREDENTIALS."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path),
        [GMAIL_COMPOSE_SCOPE],
    )
    credentials = flow.run_local_server(port=0, open_browser=True)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")

    print("Gmail OAuth setup completed.")
    print(f"Token: {token_path}")
    print("Scope: gmail.compose")
    print("Smart Office email sending remains disabled; only draft creation is implemented.")


if __name__ == "__main__":
    main()
