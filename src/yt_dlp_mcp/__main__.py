from __future__ import annotations

import logging
import sys

from .config import Config
from .server import run_server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    config = Config.from_env()
    run_server(config)


if __name__ == "__main__":
    main()
