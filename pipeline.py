import os
import sys
import json
import logging
from pathlib import Path

import pandas as pd
import snowflake.connector
from dotenv import load_dotenv

from logging_config import setup_logging, get_logger


# 1. Project paths and table names
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

CLEAN_FILE = DATA_DIR / "clean_sales_data.csv"
REPORT_FILE = DATA_DIR / "data_quality_report.json"

RAW_TABLE = "AI_DATA_ENGINEERING_DB.RAW.RAW_SALES_DATA"
CLEAN_TABLE = "AI_DATA_ENGINEERING_DB.RAW.CLEAN_SALES_DATA"

REQUIRED_ENV_VARS = [
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_DATABASE",
    "SNOWFLAKE_SCHEMA",
]

load_dotenv()

# Initialize file and console logging.
setup_logging()
logger = get_logger("AI_Data_Engineering_Pipeline")


# 2. Connect to Snowflake
def connect_to_snowflake():
    missing = [
        name for name in REQUIRED_ENV_VARS
        if not os.getenv(name)
    ]

    if missing:
        raise EnvironmentError(
            "Missing settings in .env file: " + ", ".join(missing)
        )

    logger.info("Connecting to Snowflake.")

    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA"),
        authenticator="OAUTH_AUTHORIZATION_CODE",
    )


# 3. Read raw data (read-only)
def load_raw_data(connection):
    query = f"""
        SELECT ORDER_ID, PRODUCT, QUANTITY, UNIT_PRICE, CITY
        FROM {RAW_TABLE}
    """

    cursor = connection.cursor()

    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        columns = [
            column[0].lower()
            for column in cursor.description
        ]
    finally:
        cursor.close()

    df = pd.DataFrame(rows, columns=columns)
    logger.info("Loaded %d raw records.", len(df))

    return df


# 4. Clean data and build the report
def clean_data(df):
    df = df.copy()
    original_rows = len(df)

    missing_by_column = {
        column: int(count)
        for column, count in df.isnull().sum().items()
    }
    missing_before = sum(missing_by_column.values())

    df["quantity"] = pd.to_numeric(
        df["quantity"], errors="coerce"
    )
    df["unit_price"] = pd.to_numeric(
        df["unit_price"], errors="coerce"
    )

    duplicate_ids = int(
        df.loc[df["order_id"].notna(), "order_id"]
        .duplicated().sum()
    )

    invalid_prices = int(
        (
            df["unit_price"].isna()
            | (df["unit_price"] <= 0)
        ).sum()
    )

    # Missing quantities are repaired; nonpositive quantities are removed.
    invalid_quantities = int(
        (df["quantity"].notna() & (df["quantity"] <= 0)).sum()
    )

    # Remove rows with missing order IDs.
    df = df[df["order_id"].notna()].copy()

    # Keep the first occurrence of each order ID.
    df = df.drop_duplicates(
        subset=["order_id"], keep="first"
    ).copy()

    # Apply missing-value rules.
    df["quantity"] = df["quantity"].fillna(1)
    df["city"] = df["city"].fillna("Unknown")

    # Remove invalid records.
    df = df[
        df["unit_price"].notna()
        & (df["unit_price"] > 0)
        & df["quantity"].notna()
        & (df["quantity"] > 0)
    ].copy()

    if (df["quantity"] % 1 == 0).all():
        df["quantity"] = df["quantity"].astype(int)

    df["total_sales"] = df["quantity"] * df["unit_price"]

    report = {
        "pipeline_status": "pending_validation",
        "input_records": original_rows,
        "output_records": len(df),
        "records_removed": original_rows - len(df),
        "missing_values_detected": missing_before,
        "missing_values_by_column": missing_by_column,
        "duplicate_order_ids_detected": duplicate_ids,
        "invalid_prices_detected": invalid_prices,
        "invalid_quantities_detected": invalid_quantities,
        "cleaned_file": CLEAN_FILE.name,
        "source": "Snowflake RAW_SALES_DATA",
    }

    logger.info(
        "Cleaning completed: %d input records, %d output records.",
        original_rows, len(df)
    )

    return df, report


# 5. Validate cleaned data
def validate_data(df):
    errors = []

    if df.empty:
        errors.append("No valid records left after cleaning.")

    if df["order_id"].isna().any():
        errors.append("Missing order IDs found.")

    if df["order_id"].duplicated().any():
        errors.append("Duplicate order IDs found.")

    if (
        df["unit_price"].isna().any()
        or (df["unit_price"] <= 0).any()
    ):
        errors.append("Invalid unit prices found.")

    if (
        df["quantity"].isna().any()
        or (df["quantity"] <= 0).any()
    ):
        errors.append("Invalid quantities found.")

    if df["city"].isna().any():
        errors.append("Missing city values found.")

    expected_totals = df["quantity"] * df["unit_price"]

    if not (
        (df["total_sales"] - expected_totals).abs() < 0.000001
    ).all():
        errors.append("Incorrect total_sales calculations found.")

    return errors


# Helper: convert pandas/numpy values to Python values.
def to_python_value(value):
    if pd.isna(value):
        return None

    if hasattr(value, "item"):
        return value.item()

    return value


# 6. Save clean data to Snowflake
# The table must already exist; do not run CREATE TABLE on every execution.
def save_clean_data_to_snowflake(connection, df):
    cursor = connection.cursor()

    columns = [
        "order_id",
        "product",
        "quantity",
        "unit_price",
        "city",
        "total_sales",
    ]

    rows = [
        tuple(to_python_value(value) for value in record)
        for record in df[columns].itertuples(
            index=False, name=None
        )
    ]

    insert_sql = f"""
        INSERT INTO {CLEAN_TABLE}
        (ORDER_ID, PRODUCT, QUANTITY, UNIT_PRICE, CITY, TOTAL_SALES)
        VALUES (%s, %s, %s, %s, %s, %s)
    """

    try:
        # Only modify the clean table, never the raw table.
        cursor.execute("BEGIN")

        try:
            cursor.execute(f"DELETE FROM {CLEAN_TABLE}")

            if rows:
                cursor.executemany(insert_sql, rows)

            # Verify the saved count with one small query.
            cursor.execute(
                f"SELECT COUNT(*) FROM {CLEAN_TABLE}"
            )
            saved_count = cursor.fetchone()[0]

            if saved_count != len(rows):
                raise ValueError(
                    f"Expected {len(rows)} rows but found "
                    f"{saved_count} in Snowflake."
                )

            cursor.execute("COMMIT")

        except Exception:
            cursor.execute("ROLLBACK")
            raise

        logger.info(
            "Saved and verified %d clean records in Snowflake.",
            len(rows)
        )

        return len(rows)

    finally:
        cursor.close()


# 7. Write the JSON quality report
def write_report(report):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with open(REPORT_FILE, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=4, default=str)


# 8. Run the complete pipeline
def run_pipeline():
    logger.info("Pipeline execution started.")
    print("\n=== AI Data Engineering Pipeline ===")

    connection = None
    report = None

    try:
        print("\nConnecting to Snowflake...")
        connection = connect_to_snowflake()
        print("Connection successful!")
        logger.info("Snowflake connection successful.")

        raw_df = load_raw_data(connection)
        print(f"Raw records loaded: {len(raw_df)}")

        clean_df, report = clean_data(raw_df)

        # Validate before writing any cleaned data.
        validation_errors = validate_data(clean_df)

        report["validation_passed"] = not validation_errors
        report["validation_errors"] = validation_errors

        if validation_errors:
            report["pipeline_status"] = "failed_validation"

            logger.error(
                "Data validation failed: %s", validation_errors
            )

            for error in validation_errors:
                print("-", error)

            write_report(report)
            print("\nCleaned data was not saved.")
            return False

        print("\nAll automated validation checks passed!")
        logger.info("All data-quality checks passed.")

        # Save to Snowflake first, then write the local CSV.
        saved_rows = save_clean_data_to_snowflake(
            connection, clean_df
        )

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        clean_df.to_csv(CLEAN_FILE, index=False)

        report["snowflake_rows_saved"] = saved_rows
        report["validation_passed"] = True
        report["pipeline_status"] = "success"

        write_report(report)

        print("\n--- Data Quality Report ---")
        for key, value in report.items():
            print(f"{key}: {value}")

        print("\nCleaned data preview:")
        print(clean_df.to_string(index=False))

        print("\nPipeline completed successfully!")
        logger.info("Pipeline completed successfully.")

        return True

    except Exception as error:
        logger.exception("Pipeline execution failed.")
        print("\nPipeline failed.")
        print(type(error).__name__, error)

        try:
            if report is None:
                report = {}

            report["pipeline_status"] = "error"
            report["error_message"] = (
                f"{type(error).__name__}: {error}"
            )
            write_report(report)

        except Exception:
            logger.exception("Could not write the failure report.")

        return False

    finally:
        if connection is not None:
            try:
                connection.close()
                logger.info("Snowflake connection closed.")
                print("\nSnowflake connection closed.")
            except Exception:
                logger.exception("Error closing Snowflake connection.")


if __name__ == "__main__":
    success = run_pipeline()
    sys.exit(0 if success else 1)
    