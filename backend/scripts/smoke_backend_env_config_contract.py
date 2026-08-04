from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def main() -> None:
    template = read("backend/.env.local.example")
    loader = read("backend/scripts/import_backend_env.ps1")
    launcher = read("backend/scripts/start_backend_configured.ps1")
    gitignore = read(".gitignore")

    required = {
        "SMART_OFFICE_SALES_AGENT_ENABLED": "true",
        "SMART_OFFICE_SALES_PROACTIVE_ENABLED": "true",
        "SMART_OFFICE_SALES_HUMOUR_ENABLED": "true",
        "SMART_OFFICE_SALES_PROFILE_PERSISTENCE_ENABLED": "true",
        "SMART_OFFICE_SALES_TELEMETRY_ENABLED": "true",
        "SMART_OFFICE_REALTIME_MODE": "quality",
        "SMART_OFFICE_SEMANTIC_ROUTER_MODE": "unified",
        "OPENAI_SEMANTIC_ROUTER_MODEL": "gpt-5.6-luna",
    }
    for name, value in required.items():
        assert f"{name}={value}" in template, f"Missing Backend default: {name}"

    assert "OPENAI_API_KEY=" not in template
    assert "CreateFromTemplate" in loader
    assert "Merge-MissingDefaults" in loader
    assert "Get-EnvEntryKey" in loader
    assert "AppendAllText" in loader
    assert "SetEnvironmentVariable($name, $value, 'Process')" in loader
    assert "Invalid Backend environment entry" in loader
    assert "Invalid environment variable name" in loader
    assert "import_backend_env.ps1" in launcher
    assert "start_backend_realtime.ps1" in launcher
    assert 'Join-Path $backendDirectory ".env.local"' in launcher
    assert 'Join-Path $backendDirectory ".env.local.example"' in launcher
    assert "-CreateFromTemplate" in launcher
    assert "backend/.env.local" in gitignore
    assert "!backend/.env.local.example" in gitignore

    print(
        "PASS: Backend flags and unified semantic routing defaults are stored in an "
        "ignored backend/.env.local, created or safely extended from the tracked "
        "template and loaded before the stable Realtime/Office launcher starts; "
        "OPENAI_API_KEY remains interactive."
    )


if __name__ == "__main__":
    main()
