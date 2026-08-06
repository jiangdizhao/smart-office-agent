from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def require(source: str, token: str, message: str) -> None:
    if token not in source:
        raise AssertionError(message)


def forbid(source: str, token: str, message: str) -> None:
    if token in source:
        raise AssertionError(message)


def main() -> None:
    services = read("backend/app/openai_services.py")
    pool = read("backend/app/openai_http_client.py")
    main_api = read("backend/app/main.py")
    office_sequence = read("backend/app/office_sequence.py")
    verifier = read("backend/app/presentation_verifier.py")

    require(
        services,
        "shared_openai_http_client",
        "OpenAI calls must use the process-local keep-alive connection pool.",
    )
    require(
        services,
        "pooled_http=true",
        "Pooled OpenAI request latency must remain traceable in Backend logs.",
    )
    forbid(
        services,
        "async with httpx.AsyncClient",
        "OpenAI calls must not recreate a TCP/TLS client for every request.",
    )
    require(pool, "max_keepalive_connections", "The OpenAI pool must bound keep-alive connections.")
    require(pool, "keepalive_expiry", "The OpenAI pool must have an explicit keep-alive expiry.")
    require(pool, "asyncio.Lock", "Lazy pool creation must be concurrency-safe.")

    require(
        main_api,
        '@app.get("/agent/tasks/{task_id}/events")',
        "The Backend SSE task event endpoint must remain available.",
    )
    require(
        main_api,
        '@app.post("/agent/tasks/{task_id}/cancel"',
        "Explicit task cancellation must remain available after removing implicit VAD cancellation.",
    )
    require(
        office_sequence,
        'event_type="verification_result"',
        "Background Office tasks must continue publishing verification results.",
    )
    require(
        office_sequence,
        'event_type="approval_required"',
        "Outlook approval gates must remain intact.",
    )
    require(
        verifier,
        "verify_presentation_tool_result",
        "PowerPoint verification must not be removed by latency optimization.",
    )
    require(
        verifier,
        "poll_interval_seconds",
        "PowerPoint verification polling must remain bounded and observable.",
    )

    print("Latency optimization backend contract passed.")


if __name__ == "__main__":
    main()
