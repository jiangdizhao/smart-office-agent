"""Smart Office Backend package runtime services."""

import multiprocessing

# Windows Office worker processes import the ``app`` package under the spawn
# start method. They must not create a second task watchdog thread. The main
# Backend process owns the single watchdog service.
if multiprocessing.current_process().name == "MainProcess":
    from app.task_watchdog import task_watchdog
else:
    task_watchdog = None

__all__ = ["task_watchdog"]
