"""Entry point for running the bot."""

import logging
import sys
import traceback

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

try:
    from typer_bot.bot import main

    if __name__ == "__main__":
        main()
except ImportError as e:
    logger.error(f"❌ Import error: {e}")
    logger.error(traceback.format_exc())
    sys.exit(1)
except Exception as e:
    logger.error(f"❌ Startup error: {e}")
    logger.error(traceback.format_exc())
    sys.exit(1)
