import argparse
import json
import time
from pathlib import Path
from urllib import error, request


def http_json(method: str, url: str, payload: dict | None = None) -> dict:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: {exc.code} {detail}") from exc


def wait_for_status(base_url: str, task_id: str, statuses: set[str], timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last_task = {}
    while time.monotonic() < deadline:
        last_task = http_json("GET", f"{base_url}/agent/tasks/{task_id}")
        if last_task["status"] in statuses:
            return last_task
        time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for {statuses}; last task={last_task}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args()

    health = http_json("GET", f"{args.base_url}/")
    assert health["status"] == "ok", health
    print("health ok")

    created = http_json(
        "POST",
        f"{args.base_url}/agent/tasks",
        {"text": "meeting prepare", "execute": False},
    )
    task_id = created["task_id"]
    print(f"created {task_id}")

    waiting = wait_for_status(args.base_url, task_id, {"waiting_approval"}, timeout=10)
    assert any(event["type"] == "approval_required" for event in waiting["events"])
    print("approval gate reached")

    approved = http_json(
        "POST",
        f"{args.base_url}/agent/tasks/{task_id}/approval",
        {"action": "skip", "note": "regression test"},
    )
    assert approved["task_id"] == task_id
    print("approval skip sent")

    finished = wait_for_status(
        args.base_url,
        task_id,
        {"completed", "failed", "cancelled"},
        timeout=10,
    )
    assert finished["status"] == "completed", finished["summary"]

    event_types = [event["type"] for event in finished["events"]]
    assert "approval_required" in event_types
    assert "approval_resolved" in event_types
    assert "completed" in event_types
    assert any(step["status"] == "skipped" for step in finished["steps"])
    print("task completed with skipped approval step")

    log_path = Path(args.repo_root) / "logs" / f"task_{task_id}.jsonl"
    assert log_path.exists(), f"missing log file: {log_path}"
    log_text = log_path.read_text(encoding="utf-8")
    assert '"type": "event"' in log_text
    assert '"type": "approval"' in log_text
    print(f"log ok {log_path}")


if __name__ == "__main__":
    main()
