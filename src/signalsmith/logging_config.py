"""Logging setup for the CLI: format, debug dump."""

import logging
import os
import sys
from typing import Any

import yaml

__all__ = ["dump_logging_config", "setup_logging"]


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else os.environ.get("PYTHON_LOGGING_LEVEL", "INFO")

    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        datefmt="%Y-%m-%dT%H:%M:%S",
        format=(
            "%(asctime)s.%(msecs)03d %(process)d %(thread)d %(levelno)03d:%(levelname)-8s "
            "%(name)-12s %(module)s:%(lineno)s:%(funcName)s %(message)s"
        ),
    )

    # httpx logs one INFO line per HTTP request; too noisy for normal runs.
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)


def dump_logging_config() -> None:
    """Dump current logging configuration for debugging."""

    config: dict[str, Any] = {
        "root_logger": {
            "level": logging.getLevelName(logging.root.level),
            "handlers": [type(h).__name__ for h in logging.root.handlers],
            "filters": [type(f).__name__ for f in logging.root.filters],
        },
        "loggers": {},
    }

    # Get all registered loggers
    for name in sorted(logging.Logger.manager.loggerDict.keys()):
        logger_obj = logging.Logger.manager.loggerDict[name]
        if isinstance(logger_obj, logging.Logger):
            config["loggers"][name] = {
                "level": logging.getLevelName(logger_obj.level),
                "propagate": logger_obj.propagate,
                "handlers": [type(h).__name__ for h in logger_obj.handlers],
                "filters": [type(f).__name__ for f in logger_obj.filters],
            }
        else:
            config["loggers"][name] = {"placeholder": True}

    print("\n=== LOGGING CONFIG ===", file=sys.stderr)
    print(yaml.dump(config, default_flow_style=False), file=sys.stderr)
    print("======================\n", file=sys.stderr)
