from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _json_request(url: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not connect to {url}: {exc}") from exc
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object from {url}")
    return value


def _route_body(text: str) -> dict[str, Any]:
    return {
        "conversation_id": "semantic-live-acceptance",
        "visit_id": None,
        "language": "zh" if any("\u3400" <= char <= "\u9fff" for char in text) else "en",
        "actor_type": "visitor",
        "text": text,
        "recent_turns": [],
        "runtime_context": {
            "interaction_panel": None,
            "active_tool": None,
            "assistant_speaking": False,
            "visitor_present": False,
        },
    }


def _assert_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message} Expected={expected!r} Actual={actual!r}")


def _assert_not_execute(response: dict[str, Any], message: str) -> None:
    if response.get("final_policy_decision") == "execute":
        raise AssertionError(f"{message} The router incorrectly allowed execution.")


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def run_offline_contract() -> None:
    from app.semantic_deterministic_router import classify_deterministic
    from app.semantic_route_models import SemanticAction, SemanticRoute, SemanticRouteRequest
    from app.semantic_route_policy import semantic_route_policy
    from app.semantic_route_validator import validate_semantic_action_evidence

    cases = [
        ("你是谁", "self_introduction", "answer_only", None),
        ("你的角色是什么", "self_introduction", "answer_only", None),
        ("你在这个展台主要负责什么", "self_introduction", "answer_only", None),
        ("What is your role here?", "self_introduction", "answer_only", None),
        ("打开 Teams", "application_action", "execute", None),
        ("请帮我启动微软团队", "application_action", "execute", None),
        ("麻烦你打开teams", "application_action", "execute", None),
        ("麻烦您启动 Microsoft Teams", "application_action", "execute", None),
        ("Could you open Teams?", "application_action", "execute", None),
        ("能不能帮我打开 OneNote？", "application_action", "execute", None),
        ("停止音乐", "system_action", "execute", None),
        ("音量设置为30%", "system_action", "execute", 30),
        ("音量设置为 30%", "system_action", "execute", 30),
        ("把音量设置到百分之三十", "system_action", "execute", 30),
        ("请你把系统声音调为三十", "system_action", "execute", 30),
        ("麻烦您把扬声器音量调到一半", "system_action", "execute", 50),
        ("把喇叭声音调到三成", "system_action", "execute", 30),
        ("先不要打开 Teams，介绍一下它能做什么", "capability_explanation", "answer_only", None),
        ("Teams 为什么总是打不开", "capability_explanation", "answer_only", None),
        ("如果打开 Teams 会发生什么", "general_question", "answer_only", None),
        ("我不是要预约，只是想了解会议预约功能", "capability_explanation", "answer_only", None),
        ("我想了解登记表会保存什么", "capability_explanation", "answer_only", None),
        ("他说打开 Teams 是什么意思", "capability_explanation", "answer_only", None),
        ("比较一下 Teams 和 OneNote，但不要打开它们", "capability_explanation", "answer_only", None),
    ]
    results: list[dict[str, Any]] = []
    for index, (text, expected_intent, expected_decision, expected_percent) in enumerate(cases):
        request = SemanticRouteRequest(
            conversation_id=f"offline-contract-{index}",
            visit_id=None,
            language="zh" if any("\u3400" <= char <= "\u9fff" for char in text) else "en",
            actor_type="visitor",
            text=text,
        )
        route = classify_deterministic(request)
        if route is None:
            raise AssertionError(f"Deterministic grammar did not classify: {text}")
        route = validate_semantic_action_evidence(route, text)
        route, final, reasons = semantic_route_policy.apply(route)
        _assert_equal(route.primary_intent, expected_intent, f"Wrong intent for {text}")
        _assert_equal(final, expected_decision, f"Wrong decision for {text}")
        if expected_percent is not None:
            _assert_equal(route.entities.get("percent"), expected_percent, f"Wrong volume for {text}")
            _assert_equal(route.actions[0].arguments.get("percent"), expected_percent, f"Wrong volume action for {text}")
        results.append(
            {
                "text": text,
                "intent": route.primary_intent,
                "decision": final,
                "source": route.source,
                "reasons": [*route.reason_codes, *reasons],
            }
        )

    contradictory = SemanticRoute(
        primary_intent="application_action",
        domain="office",
        action_mode="execute",
        confidence=0.99,
        actions=[SemanticAction(
            verb="close",
            target="teams",
            polarity="affirmed",
            speech_act="command",
            evidence="麻烦你打开teams",
        )],
        entities={"language": "zh"},
        risk="low",
        reason_codes=[],
        source="semantic_model",
        answer_engine="office_interpreter",
    )
    checked = validate_semantic_action_evidence(contradictory, "麻烦你打开teams")
    if checked.actions:
        raise AssertionError("Explicit open wording incorrectly survived as a close action.")
    if not any(code.startswith("action_verb_conflict_rejected") for code in checked.reason_codes):
        raise AssertionError("Verb conflict rejection did not leave a diagnostic reason code.")

    _print_json({"ok": True, "mode": "offline-contract", "results": results})
    print("PASS: Encoding-safe offline deterministic semantic contract completed.")


def run_live(base_url: str, skip_model_cases: bool) -> None:
    base = base_url.rstrip("/")
    print("=== Unified Semantic Router status ===")
    status = _json_request(f"{base}/api/semantic-route/status")
    _print_json(status)
    _assert_equal(status.get("ok"), True, "Semantic router status is not healthy.")
    _assert_equal(status.get("mode"), "unified", "Semantic router is not in unified mode.")

    print("\n=== Contracts ===")
    contracts = _json_request(f"{base}/api/semantic-route/contracts")
    _print_json(contracts)
    _assert_equal(contracts.get("route_schema"), "semantic-route-v1", "Unexpected route schema.")

    print("\n=== Deterministic self-test ===")
    self_test = _json_request(f"{base}/api/semantic-route/self-test", method="POST", body={})
    _print_json(self_test)
    _assert_equal(self_test.get("ok"), True, "Backend deterministic self-test failed.")

    deterministic_cases = [
        ("Canonical identity", "你是谁", "self_introduction", "answer_only"),
        ("Role paraphrase", "你的角色是什么", "self_introduction", "answer_only"),
        ("Responsibility paraphrase", "你在这个展台主要负责什么", "self_introduction", "answer_only"),
        ("English role", "What is your role here?", "self_introduction", "answer_only"),
        ("Teams command", "打开 Teams", "application_action", "execute"),
        ("Polite Teams command", "请帮我启动微软团队", "application_action", "execute"),
        ("Natural polite Teams", "麻烦你打开teams", "application_action", "execute"),
        ("English polite command", "Could you open Teams?", "application_action", "execute"),
        ("Chinese polite OneNote", "能不能帮我打开 OneNote？", "application_action", "execute"),
        ("Stop music", "停止音乐", "system_action", "execute"),
        ("Bounded volume", "音量设置为30%", "system_action", "execute"),
        ("Chinese-number volume", "把音量设置到百分之三十", "system_action", "execute"),
        ("Half volume", "麻烦您把扬声器音量调到一半", "system_action", "execute"),
        ("Negated Teams explanation", "先不要打开 Teams，介绍一下它能做什么", "capability_explanation", "answer_only"),
        ("Teams troubleshooting", "Teams 为什么总是打不开", "capability_explanation", "answer_only"),
        ("Hypothetical Teams", "如果打开 Teams 会发生什么", "general_question", "answer_only"),
        ("Booking explanation", "我不是要预约，只是想了解会议预约功能", "capability_explanation", "answer_only"),
        ("Registration explanation", "我想了解登记表会保存什么", "capability_explanation", "answer_only"),
        ("Comparison without execution", "比较一下 Teams 和 OneNote，但不要打开它们", "capability_explanation", "answer_only"),
    ]

    print("\n=== Deterministic grammar cases (route preview only) ===")
    for name, text, intent, decision in deterministic_cases:
        response = _json_request(f"{base}/api/semantic-route", method="POST", body=_route_body(text))
        route = response.get("route") or {}
        _assert_equal(route.get("primary_intent"), intent, f"{name}: wrong intent.")
        _assert_equal(response.get("final_policy_decision"), decision, f"{name}: wrong decision.")
        _assert_equal(route.get("source"), "fast_path", f"{name}: should not call Terra.")
        print(f"PASS: {name} ({response.get('elapsed_ms')} ms)")

    if not skip_model_cases:
        print("\n=== Terra open-language cases (route preview only) ===")
        model_cases = [
            "我在建筑行业做项目经理，最麻烦的是会后行动项整理。",
            "我们是一家跨国制造企业，想减少不同地区团队之间的信息遗漏。",
        ]
        for text in model_cases:
            response = _json_request(f"{base}/api/semantic-route", method="POST", body=_route_body(text))
            _assert_not_execute(response, text)
            route = response.get("route") or {}
            print(
                f"PASS: Terra case source={route.get('source')} model={response.get('model')} "
                f"elapsed_ms={response.get('elapsed_ms')}"
            )

    print("\n=== Recent decisions ===")
    recent = _json_request(f"{base}/api/semantic-route/recent-decisions?limit=20")
    _print_json(recent)
    print("\nPASS: Unified semantic router live acceptance completed.")
    print("This test previews routes only; it does not launch desktop applications.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--skip-model-cases", action="store_true")
    parser.add_argument("--offline-contract", action="store_true")
    args = parser.parse_args()
    if args.offline_contract:
        run_offline_contract()
    else:
        run_live(args.base_url, args.skip_model_cases)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
