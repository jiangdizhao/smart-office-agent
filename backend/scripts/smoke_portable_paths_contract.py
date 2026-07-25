from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
START_SCRIPT = REPO_ROOT / "backend" / "scripts" / "start_backend_realtime.ps1"
CONFIG_MODULE = REPO_ROOT / "backend" / "app" / "presentation_config.py"


def require(text: str, needle: str, message: str) -> None:
    if needle not in text:
        raise AssertionError(message)


def forbid(text: str, needle: str, message: str) -> None:
    if needle.casefold() in text.casefold():
        raise AssertionError(message)


def powershell_code_without_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def main() -> None:
    start_text = START_SCRIPT.read_text(encoding="utf-8")
    start_code = powershell_code_without_comments(start_text)
    config_text = CONFIG_MODULE.read_text(encoding="utf-8")

    for old_root in (
        r"F:\smart-office-agent",
        r"C:\smart-office-agent",
        r"D:\smart-office-agent",
    ):
        forbid(
            start_code,
            old_root,
            f"Backend startup must not hard-code repository root {old_root}.",
        )
        forbid(
            config_text,
            old_root,
            f"Runtime config must not hard-code repository root {old_root}.",
        )

    require(
        start_text,
        "$backendDirectory = Split-Path -Parent $PSScriptRoot",
        "PowerShell startup must derive its location from PSScriptRoot.",
    )
    require(
        start_text,
        "$env:SMART_OFFICE_PROJECT_ROOT = $repoRoot",
        "PowerShell startup must publish the discovered repository root.",
    )
    require(
        start_text,
        "Resolve-PortableProjectPath",
        "PowerShell startup must resolve portable project-relative paths.",
    )
    require(
        start_text,
        'DefaultRelativePath "demo_files\\Loss.pptx"',
        "PPT default must be repository-relative.",
    )
    require(
        start_text,
        'DefaultRelativePath "demo_files\\LOG"',
        "Output default must be repository-relative.",
    )
    require(
        start_text,
        'DefaultRelativePath "config\\email_recipients.json"',
        "Recipient configuration default must be repository-relative.",
    )
    require(
        start_text,
        "points to unavailable root",
        "Startup must recover from stale absolute paths saved on another computer.",
    )

    require(
        config_text,
        "REPO_ROOT = Path(__file__).resolve().parents[2]",
        "Python runtime config must derive the repository root from its own file.",
    )
    require(
        config_text,
        'os.environ.get("SMART_OFFICE_DEMO_PPT", "demo_files/Loss.pptx")',
        "Python PPT default must remain repository-relative.",
    )
    require(
        config_text,
        'os.environ.get("SMART_OFFICE_OUTPUT_DIR", "demo_files/LOG")',
        "Python output default must remain repository-relative.",
    )

    print("PASS: Smart Office runtime paths are portable across Windows drive letters.")


if __name__ == "__main__":
    main()
