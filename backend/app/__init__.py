"""Smart Office Backend package runtime services."""

import os

# Office worker processes receive this marker before Windows ``spawn`` imports
# the package. Every real Backend process, including uvicorn reload/workers, owns
# its normal task watchdog.
if os.getenv("SMART_OFFICE_WORKER_CHILD", "").strip() != "1":
    from app.task_watchdog import task_watchdog
else:
    task_watchdog = None

__all__ = ["task_watchdog"]
