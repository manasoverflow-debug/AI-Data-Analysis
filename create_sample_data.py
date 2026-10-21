import pandas as pd
from pathlib import Path

# Find the project's data folder.
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Create sample sales data.
data = {
    "order_id": [101, 102, 103, 104, 105, 105, 107, 108],
    "product": [
        "Laptop", "Mouse", "Keyboard", "Monitor",
        "Laptop", "Laptop", "Mouse", "Monitor"
    ],
    "quantity": [1, 2, 1, 1, 2, 2, 3, 1],
    "unit_price": [55000, 500, 1500, 12000, 55000, 55000, 500, 12000],
    "city": [
        "Bengaluru", "Mysuru", None, "Bengaluru",
        "Chennai", "Chennai", "Mysuru", "Bengaluru"
    ],
}

df = pd.DataFrame(data)

# Introduce a few realistic data-quality issues.
df.loc[2, "quantity"] = None
df.loc[6, "unit_price"] = -500

# Save the raw dataset.
output_file = DATA_DIR / "raw_sales_data.csv"
df.to_csv(output_file, index=False)

print("Sample dataset created successfully!")
print(f"Saved to: {output_file}")
print(f"Total rows: {len(df)}")
print("\nDataset preview:")
print(df.to_string(index=False))
