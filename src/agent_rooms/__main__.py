import logging
import sys

import uvicorn

from agent_rooms.bootstrap import create_app
from agent_rooms.settings import Settings


def main() -> None:
    try:
        settings = Settings.from_env()
        app = create_app(settings)
    except ValueError as error:
        sys.exit(f"agent-rooms: {error}")
    logging.basicConfig(level=settings.log_level.upper())
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        workers=1,
        log_level=settings.log_level.lower(),
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
