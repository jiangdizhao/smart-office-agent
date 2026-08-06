from __future__ import annotations

import getpass
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.result_center_auth import hash_admin_password  # noqa: E402


def main() -> int:
    first = getpass.getpass("Administrator password: ")
    second = getpass.getpass("Confirm administrator password: ")
    if not first:
        print("ERROR: password cannot be empty.", file=sys.stderr)
        return 2
    if first != second:
        print("ERROR: passwords do not match.", file=sys.stderr)
        return 2
    print(hash_admin_password(first))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
