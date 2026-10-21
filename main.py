import sys

from logging_config import setup_logging, get_logger
from pipeline import run_pipeline
from transform_data import transform_data


# Set up logging (console + logs/pipeline.log)
setup_logging()
logger = get_logger("Main")


def main():
    logger.info("Overall process started.")

    # Stage 1: Snowflake pipeline (read, clean, validate, save)
    logger.info("Stage 1 started: Snowflake data pipeline.")
    pipeline_ok = run_pipeline()

    if not pipeline_ok:
        logger.error("Stage 1 failed. Stopping before the transformation.")
        logger.error("Overall result: FAILED.")
        return False

    logger.info("Stage 1 completed successfully.")

    # Stage 2: Local transformation (only runs if Stage 1 succeeded)
    logger.info("Stage 2 started: data transformation.")
    transform_ok = transform_data()

    if not transform_ok:
        logger.error("Stage 2 failed.")
        logger.error("Overall result: FAILED.")
        return False

    logger.info("Stage 2 completed successfully.")
    logger.info("Overall result: SUCCESS.")
    return True


if __name__ == "__main__":
    try:
        success = main()
    except Exception:
        logger.exception("Unexpected error in main.py.")
        success = False

    # Exit code 0 = success, 1 = failure
    sys.exit(0 if success else 1)
    