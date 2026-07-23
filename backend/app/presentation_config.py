from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_repo_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (REPO_ROOT / candidate).resolve()


@dataclass(frozen=True)
class PresentationRuntimeConfig:
    presentation_path: Path
    output_directory: Path
    target_monitor_device: str
    target_monitor_number: int
    outlook_sender_email: str
    recipient_name: str
    recipient_email: str
    close_powerpoint_when_empty: bool = False

    @classmethod
    def from_environment(cls) -> "PresentationRuntimeConfig":
        return cls(
            presentation_path=_resolve_repo_path(
                os.environ.get("SMART_OFFICE_DEMO_PPT", "demo_files/Loss.pptx")
            ),
            output_directory=_resolve_repo_path(
                os.environ.get("SMART_OFFICE_OUTPUT_DIR", "demo_files/LOG")
            ),
            target_monitor_device=os.environ.get(
                "SMART_OFFICE_PRESENTATION_MONITOR_DEVICE",
                r"\\.\DISPLAY2",
            ),
            target_monitor_number=max(
                1,
                int(os.environ.get("SMART_OFFICE_PRESENTATION_MONITOR_NUMBER", "2")),
            ),
            outlook_sender_email=os.environ.get(
                "SMART_OFFICE_OUTLOOK_SENDER_EMAIL",
                "jiangdizhao1@outlook.com",
            ).strip(),
            recipient_name=os.environ.get("SMART_OFFICE_DEMO_RECIPIENT_NAME", "Rico"),
            recipient_email=os.environ.get(
                "SMART_OFFICE_DEMO_RECIPIENT_EMAIL",
                "jiangdizhao@gmail.com",
            ).strip(),
            close_powerpoint_when_empty=os.environ.get(
                "SMART_OFFICE_CLOSE_POWERPOINT_WHEN_EMPTY",
                "false",
            ).casefold()
            in {"1", "true", "yes", "on"},
        )

    def public_dict(self) -> dict:
        payload = asdict(self)
        payload["presentation_path"] = str(self.presentation_path)
        payload["presentation_path_relative"] = _relative_or_absolute(self.presentation_path)
        payload["presentation_exists"] = self.presentation_path.is_file()
        payload["output_directory"] = str(self.output_directory)
        payload["output_directory_relative"] = _relative_or_absolute(self.output_directory)
        payload["output_directory_exists"] = self.output_directory.is_dir()
        payload["email_send_enabled"] = False
        payload["automation"] = "powerpoint_com"
        return payload


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


presentation_config = PresentationRuntimeConfig.from_environment()
