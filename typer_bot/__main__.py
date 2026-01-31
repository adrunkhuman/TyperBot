"""Entry point for running the bot."""

import logging
import sys

# Logging FIRST - configure logging early in the application lifecycle.
# Discord's stderr handler is prevented by passing log_handler=None to bot.run().
from typer_bot.utils.logger import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

try:
    from typer_bot.bot import main

    if __name__ == "__main__":
        main()
except ImportError:
    logger.exception("❌ Import error during startup")
    sys.exit(1)
except Exception:
    logger.exception("❌ Startup error")
    sys.exit(1)
