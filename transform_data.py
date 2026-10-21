import logging
from pathlib import Path

import pandas as pd

from logging_config import setup_logging, get_logger


# Project paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

INPUT_FILE = DATA_DIR / "clean_sales_data.csv"
CITY_OUTPUT_FILE = DATA_DIR / "sales_by_city.csv"
PRODUCT_OUTPUT_FILE = DATA_DIR / "sales_by_product.csv"
SUMMARY_OUTPUT_FILE = DATA_DIR / "sales_summary.csv"


# Configure logging
setup_logging()
logger = get_logger("Data_Transformation")


def transform_data():
    try:
        logger.info("Starting data transformation.")

        # Check whether the input file exists
        if not INPUT_FILE.exists():
            logger.error("Cleaned sales data file not found: %s", INPUT_FILE)
            return False

        # Load cleaned data locally
        df = pd.read_csv(INPUT_FILE)

        if df.empty:
            logger.error("The cleaned sales data file is empty.")
            return False

        # Check required columns
        required_columns = [
            "order_id",
            "product",
            "quantity",
            "unit_price",
            "city",
            "total_sales",
        ]

        missing_columns = [
            column for column in required_columns
            if column not in df.columns
        ]

        if missing_columns:
            logger.error(
                "Missing required columns: %s",
                missing_columns,
            )
            return False

        # Convert numeric columns safely
        for column in ["quantity", "unit_price", "total_sales"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        if df[required_columns].isnull().any().any():
            logger.error("Required columns contain missing or invalid values.")
            return False

        # 1. Sales grouped by city
        sales_by_city = (
            df.groupby("city", as_index=False)
            .agg(
                total_orders=("order_id", "nunique"),
                total_quantity=("quantity", "sum"),
                total_sales=("total_sales", "sum"),
            )
            .sort_values("total_sales", ascending=False)
        )

        # 2. Sales grouped by product
        sales_by_product = (
            df.groupby("product", as_index=False)
            .agg(
                total_orders=("order_id", "nunique"),
                total_quantity=("quantity", "sum"),
                total_sales=("total_sales", "sum"),
            )
            .sort_values("total_sales", ascending=False)
        )

        # 3. Overall sales summary
        total_orders = df["order_id"].nunique()
        total_quantity = df["quantity"].sum()
        total_sales = df["total_sales"].sum()

        average_order_value = (
            total_sales / total_orders if total_orders else 0
        )

        summary = pd.DataFrame(
            [
                {
                    "total_orders": total_orders,
                    "total_quantity_sold": total_quantity,
                    "total_sales": round(total_sales, 2),
                    "average_order_value": round(
                        average_order_value, 2
                    ),
                }
            ]
        )

        # Save transformation results locally
        sales_by_city.to_csv(CITY_OUTPUT_FILE, index=False)
        sales_by_product.to_csv(PRODUCT_OUTPUT_FILE, index=False)
        summary.to_csv(SUMMARY_OUTPUT_FILE, index=False)

        logger.info("City sales saved to %s", CITY_OUTPUT_FILE)
        logger.info("Product sales saved to %s", PRODUCT_OUTPUT_FILE)
        logger.info("Sales summary saved to %s", SUMMARY_OUTPUT_FILE)
        logger.info("Data transformation completed successfully.")

        print("\nDATA TRANSFORMATION COMPLETED")
        print("--------------------------------")
        print(f"Total orders: {total_orders}")
        print(f"Total quantity sold: {total_quantity}")
        print(f"Total sales: {total_sales:.2f}")
        print(f"Average order value: {average_order_value:.2f}")

        print("\nSales by city:")
        print(sales_by_city.to_string(index=False))

        print("\nSales by product:")
        print(sales_by_product.to_string(index=False))

        return True

    except Exception:
        logger.exception("Data transformation failed.")
        return False


if __name__ == "__main__":
    success = transform_data()
    raise SystemExit(0 if success else 1)
