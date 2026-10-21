import os
import snowflake.connector
from dotenv import load_dotenv

# Load Snowflake settings from .env
load_dotenv()

connection = None
cursor = None

try:
    print("Connecting to Snowflake...")

    connection = snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA"),
        authenticator="OAUTH_AUTHORIZATION_CODE",
    )

    print("Connection successful!")

    cursor = connection.cursor()

    # Check the current Snowflake session
    cursor.execute("""
        SELECT
            CURRENT_USER(),
            CURRENT_ROLE(),
            CURRENT_DATABASE(),
            CURRENT_WAREHOUSE()
    """)

    session_details = cursor.fetchone()

    print("\nSnowflake Session Details")
    print("-------------------------")
    print("User:", session_details[0])
    print("Role:", session_details[1])
    print("Database:", session_details[2])
    print("Warehouse:", session_details[3])

    # Count records in the raw sales table
    cursor.execute("""
        SELECT COUNT(*)
        FROM AI_DATA_ENGINEERING_DB.RAW.RAW_SALES_DATA
    """)

    total_records = cursor.fetchone()[0]

    print("\nSales Data Verification")
    print("----------------------")
    print("Total raw sales records:", total_records)

    # Display a sample of the raw sales data
    cursor.execute("""
        SELECT *
        FROM AI_DATA_ENGINEERING_DB.RAW.RAW_SALES_DATA
        ORDER BY ORDER_ID
        LIMIT 5
    """)

    records = cursor.fetchall()

    print("\nSample Sales Records")
    print("--------------------")

    for record in records:
        print(record)

except Exception as error:
    print("\nConnection or query failed.")
    print("Error type:", type(error).__name__)
    print("Error details:", error)

finally:
    if cursor is not None:
        cursor.close()

    if connection is not None:
        connection.close()
        print("\nSnowflake connection closed.")
        