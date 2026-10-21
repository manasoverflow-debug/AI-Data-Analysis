import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler


# Locate the project directory
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "pipeline.log"


def setup_logging():
    # Create the logs directory if it doesn't exist
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Record logs in a file, keeping the log file manageable
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8"
    )

    # Also show logs in the VS Code terminal
    console_handler = logging.StreamHandler()

    # Use the same format for both outputs
    log_format = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler.setFormatter(log_format)
    console_handler.setFormatter(log_format)

    # Configure the root logger only once
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if not root_logger.handlers:
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)


def get_logger(name):
    return logging.getLogger(name)
