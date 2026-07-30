from __future__ import annotations

import uvicorn

from app.config import load_config
from app.main import app


def main() -> None:
    config, _ = load_config()
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
