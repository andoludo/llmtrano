"""Logging for long-running processes (api, worker), whose runners configure only their own loggers."""

import logging

FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """Give the `llmtrano` logger a stderr handler once; messages are structured key=value text."""
    logger = logging.getLogger("llmtrano")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(FORMAT))
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
