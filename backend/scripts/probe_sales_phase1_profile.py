from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TEMP_DIR = tempfile.TemporaryDirectory(prefix="smart-office-sales-probe-")
os.environ["SMART_OFFICE_CONTACT_DB"] = str(Path(_TEMP_DIR.name) / "contacts.sqlite3")
os.environ["SMART_OFFICE_SALES_AGENT_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_PROACTIVE_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_HUMOUR_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_PROFILE_PERSISTENCE_ENABLED"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.sales_policy import sales_runtime_policy  # noqa: E402
from app.sales_profile_extractor import sales_profile_extractor  # noqa: E402

text = "我们公司是做建筑行业，我负责运营管理，最大的问题是会议后没人跟进，也需要会议总结。"
print("FLAGS", json.dumps(sales_runtime_policy.feature_flags().model_dump(), ensure_ascii=False))
print("EXTRACTION", json.dumps(sales_profile_extractor.extract(text, language="zh").model_dump(), ensure_ascii=False))
with TestClient(app) as client:
    response = client.post(
        "/api/sales/turn",
        json={
            "conversation_id": "probe-conversation",
            "visit_id": "probe-visit",
            "text": text,
            "language": "zh",
            "actor_type": "visitor",
            "recent_context": "",
        },
    )
    print("STATUS", response.status_code)
    print("RESPONSE", json.dumps(response.json(), ensure_ascii=False))
_TEMP_DIR.cleanup()
