"""Entry point for running the bot."""

import logging
import sys

from typer_bot.utils.logger import setup_logging

# Logging first - prevents discord.py from hijacking the root logger
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
