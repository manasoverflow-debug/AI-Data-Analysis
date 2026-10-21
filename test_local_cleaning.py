import pandas as pd

from pipeline import clean_data, validate_data


sample = pd.DataFrame({
    "order_id": [101, 102, 102, 103, None],
    "product": ["Laptop", "Mouse", "Mouse", "Keyboard", "Monitor"],
    "quantity": [1, None, 2, 1, 1],
    "unit_price": [55000, 500, 500, -1500, 12000],
    "city": ["Bengaluru", None, None, "Mysuru", "Chennai"],
})

cleaned, report = clean_data(sample)
errors = validate_data(cleaned)

print("CLEANED DATA")
print(cleaned.to_string(index=False))

print("\nQUALITY REPORT")
print(report)

print("\nVALIDATION ERRORS")
print(errors)
