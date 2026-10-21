import os
import json
import unittest
from pathlib import Path

import pandas as pd
import snowflake.connector
from dotenv import load_dotenv

from pipeline import validate_data


# 1. Project paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

CLEAN_FILE = DATA_DIR / "clean_sales_data.csv"
REPORT_FILE = DATA_DIR / "data_quality_report.json"

RAW_TABLE = "AI_DATA_ENGINEERING_DB.RAW.RAW_SALES_DATA"
CLEAN_TABLE = "AI_DATA_ENGINEERING_DB.RAW.CLEAN_SALES_DATA"

load_dotenv()


# 2. Connect to Snowflake using the same working authentication
def connect_to_snowflake():
    required = [
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA",
    ]

    missing = [name for name in required if not os.getenv(name)]

    if missing:
        raise EnvironmentError(
            "Missing .env settings: " + ", ".join(missing)
        )

    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA"),
        authenticator="OAUTH_AUTHORIZATION_CODE",
    )


# 3. Read data directly from Snowflake
def read_snowflake_table(connection, table_name):
    cursor = connection.cursor()

    try:
        cursor.execute(f"SELECT * FROM {table_name}")
        rows = cursor.fetchall()
        columns = [column[0].lower() for column in cursor.description]

        return pd.DataFrame(rows, columns=columns)

    finally:
        cursor.close()


class TestDataPipeline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Read both tables from Snowflake; no raw CSV required.
        cls.connection = connect_to_snowflake()

        cls.raw = read_snowflake_table(
            cls.connection, RAW_TABLE
        )

        cls.snowflake_clean = read_snowflake_table(
            cls.connection, CLEAN_TABLE
        )

        # These are outputs created by pipeline.py.
        if not CLEAN_FILE.exists():
            cls.connection.close()
            raise FileNotFoundError(
                "Cleaned CSV not found. Run pipeline.py first."
            )

        if not REPORT_FILE.exists():
            cls.connection.close()
            raise FileNotFoundError(
                "Quality report not found. Run pipeline.py first."
            )

        cls.clean = pd.read_csv(CLEAN_FILE)

        with open(REPORT_FILE, "r", encoding="utf-8") as file:
            cls.report = json.load(file)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "connection", None) is not None:
            cls.connection.close()

    # Test 1: Raw data is available in Snowflake
    def test_raw_data_exists(self):
        self.assertGreater(len(self.raw), 0)

    # Test 2: Cleaned output files exist
    def test_output_files_exist(self):
        self.assertTrue(CLEAN_FILE.exists())
        self.assertTrue(REPORT_FILE.exists())

    # Test 3: Cleaned data is not empty
    def test_cleaned_data_not_empty(self):
        self.assertGreater(len(self.clean), 0)

    # Test 4: Order IDs are unique
    def test_unique_order_ids(self):
        self.assertFalse(self.clean["order_id"].isna().any())
        self.assertFalse(
            self.clean["order_id"].duplicated().any()
        )

    # Test 5: No missing values remain
    def test_no_missing_values(self):
        self.assertEqual(
            int(self.clean.isnull().sum().sum()), 0
        )

    # Test 6: Prices are positive
    def test_prices_are_positive(self):
        self.assertTrue(
            (self.clean["unit_price"] > 0).all()
        )

    # Test 7: Quantities are positive
    def test_quantities_are_positive(self):
        self.assertTrue(
            (self.clean["quantity"] > 0).all()
        )

    # Test 8: Sales totals are correct
    def test_sales_calculations(self):
        expected = (
            self.clean["quantity"] * self.clean["unit_price"]
        )

        difference = (
            self.clean["total_sales"] - expected
        ).abs()

        self.assertTrue((difference < 0.000001).all())

    # Test 9: Snowflake raw table has the expected record count
    def test_raw_table_count(self):
        self.assertEqual(len(self.raw), 78)

    # Test 10: CSV and Snowflake clean table counts match
    def test_clean_counts_match(self):
        self.assertEqual(
            len(self.clean), len(self.snowflake_clean)
        )

        self.assertEqual(
            len(self.clean), self.report["output_records"]
        )

    # Test 11: Snowflake and CSV records match
    def test_clean_records_match(self):
        columns = [
            "order_id", "product", "quantity",
            "unit_price", "city", "total_sales"
        ]

        csv_data = self.clean[columns].copy()
        snowflake_data = self.snowflake_clean[columns].copy()

        csv_data = csv_data.sort_values(
            "order_id"
        ).reset_index(drop=True)

        snowflake_data = snowflake_data.sort_values(
            "order_id"
        ).reset_index(drop=True)

        pd.testing.assert_frame_equal(
            csv_data,
            snowflake_data,
            check_dtype=False,
            check_exact=False,
            rtol=1e-6,
            atol=1e-6
        )

    # Test 12: The quality report is successful
    def test_quality_report(self):
        self.assertEqual(
            self.report["pipeline_status"], "success"
        )

        self.assertTrue(
            self.report.get("validation_passed", False)
        )

        self.assertEqual(
            self.report.get("validation_errors", []), []
        )

        self.assertEqual(
            self.report["input_records"], len(self.raw)
        )

        self.assertEqual(
            self.report["output_records"], len(self.clean)
        )

    # Test 13: Valid data passes validation
    def test_validation_accepts_valid_data(self):
        valid = pd.DataFrame({
            "order_id": [201],
            "product": ["Keyboard"],
            "quantity": [2],
            "unit_price": [500.0],
            "city": ["Bengaluru"],
            "total_sales": [1000.0],
        })

        self.assertEqual(validate_data(valid), [])

    # Test 14: Duplicate IDs are detected
    def test_validation_detects_duplicates(self):
        invalid = pd.DataFrame({
            "order_id": [201, 201],
            "product": ["Keyboard", "Mouse"],
            "quantity": [2, 3],
            "unit_price": [500.0, 200.0],
            "city": ["Bengaluru", "Chennai"],
            "total_sales": [1000.0, 600.0],
        })

        self.assertIn(
            "Duplicate order IDs found.",
            validate_data(invalid)
        )

    # Test 15: Invalid prices are detected
    def test_validation_detects_invalid_prices(self):
        invalid = pd.DataFrame({
            "order_id": [201],
            "product": ["Keyboard"],
            "quantity": [2],
            "unit_price": [-500.0],
            "city": ["Bengaluru"],
            "total_sales": [-1000.0],
        })

        self.assertIn(
            "Invalid unit prices found.",
            validate_data(invalid)
        )

    # Test 16: Invalid quantities are detected
    def test_validation_detects_invalid_quantities(self):
        invalid = pd.DataFrame({
            "order_id": [201],
            "product": ["Keyboard"],
            "quantity": [0],
            "unit_price": [500.0],
            "city": ["Bengaluru"],
            "total_sales": [0.0],
        })

        self.assertIn(
            "Invalid quantities found.",
            validate_data(invalid)
        )

    # Test 17: Incorrect sales totals are detected
    def test_validation_detects_incorrect_totals(self):
        invalid = pd.DataFrame({
            "order_id": [201],
            "product": ["Keyboard"],
            "quantity": [2],
            "unit_price": [500.0],
            "city": ["Bengaluru"],
            "total_sales": [900.0],
        })

        self.assertIn(
            "Incorrect total_sales calculations found.",
            validate_data(invalid)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
    