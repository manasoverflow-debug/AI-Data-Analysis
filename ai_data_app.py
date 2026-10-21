"""
AI Data Assistant
-----------------
1. Upload a CSV file.
2. Type what you want (clean it, show a table, make a chart, build a dashboard...).
3. Google Gemini writes pandas/plotly code, the app runs it and shows the result.

Run with:  streamlit run ai_data_app.py
"""

import ast
import io
import json
import os
import re

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

# ---- Connect to the other project files ----
from logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger("AI_Data_App")

try:
    import pipeline          # pipeline.py: Snowflake connection, validation, saving
    import transform_data as td   # transform_data.py: sales summaries
    PROJECT_OK = True
    IMPORT_ERROR = ""
except Exception as import_error:   # the app still works without Snowflake
    PROJECT_OK = False
    IMPORT_ERROR = str(import_error)

SALES_COLUMNS = ["order_id", "product", "quantity", "unit_price", "city", "total_sales"]

# You can change the model name in your .env file with GEMINI_MODEL
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

SYSTEM_PROMPT = """You are a data analyst assistant. The user has a pandas DataFrame
called `df` and gives you an instruction. Reply with ONLY one Python code block.

Already available (do NOT import anything): pd, np, px (plotly.express), go (plotly.graph_objects), df.

Rules:
- Do not read or write files, use the network, or import modules.
- The data can be about ANYTHING. Never assume column names: use exactly the names in
  the data summary (they may contain spaces or capital letters).
- If the user mentions a column that does not exist, pick the closest match, or explain
  the problem in `message` instead of crashing.
- Text columns that look like dates or numbers should be converted with pd.to_datetime
  or pd.to_numeric(errors="coerce") before using them.
- Never delete rows or columns unless the user asked for it (cleaning, removing, filtering).
- For big data, aggregate (groupby) or sample before plotting so charts stay readable.
- Choose chart types that fit the data (bar for categories, line for dates, histogram
  for distributions, scatter for two numbers).
- If the user asks to clean / fix / change the data, reassign `df` to the cleaned
  DataFrame (for example: df = df.drop_duplicates()). Handle missing values,
  duplicates, wrong types and invalid values sensibly, and say what you did.
- To show a table, put it in a list called `tables` as (title, DataFrame) tuples.
- To show charts or graphs, put plotly figures in a list called `figures`.
  Give every figure a clear title.
- For a dashboard: fill `metrics` (a dict such as {"Total Sales": 12345}) with the
  key numbers AND add several different charts to `figures`.
- Always set `message` to a short, plain-English summary of what you did or found.
- If the instruction is only a question, answer it in `message` (and add a table if useful).
"""

# Names and pandas functions the AI is not allowed to use (file access, code running)
BLOCKED_NAMES = {
    "open", "eval", "exec", "compile", "input", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "breakpoint",
}
BLOCKED_ATTRS = {
    "read_csv", "read_excel", "read_html", "read_json", "read_pickle",
    "read_sql", "read_parquet", "to_csv", "to_excel", "to_pickle",
    "to_sql", "to_parquet", "write_html", "write_image", "write_json",
}


# ---------- Helpers ----------

def describe_data(df):
    """A small text summary of the data that we send to the AI (not the whole file)."""
    max_cols = 60
    lines = [
        f"Rows: {len(df)}, Columns: {len(df.columns)}",
        "Columns (name | type | missing | unique | examples):",
    ]

    for col in list(df.columns)[:max_cols]:
        examples = [str(v)[:30] for v in df[col].dropna().unique()[:3]]
        lines.append(
            f"- {col!r} | {df[col].dtype} | missing: {int(df[col].isna().sum())}"
            f" | unique: {df[col].nunique()} | e.g. {', '.join(examples)}"
        )

    if len(df.columns) > max_cols:
        lines.append(f"... and {len(df.columns) - max_cols} more columns not shown")

    lines.append(f"Duplicate rows: {int(df.duplicated().sum())}")

    numeric = df.select_dtypes(include="number")
    if not numeric.empty:
        lines.append("Numeric column statistics:")
        lines.append(numeric.iloc[:, :20].describe().T.to_string())

    lines.append("First 5 rows:")
    lines.append(df.iloc[:, :30].head(5).to_string())
    return "\n".join(lines)


def extract_code(text):
    match = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def ask_ai(client, df, instruction, previous_code=None, error=None):
    content = f"Data summary:\n{describe_data(df)}\n\nUser instruction: {instruction}"

    if previous_code and error:
        content += (
            f"\n\nYour previous code failed.\nCode:\n{previous_code}\n\n"
            f"Error:\n{error}\n\nReturn corrected code."
        )

    response = client.models.generate_content(
        model=MODEL,
        contents=content,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=4000,
        ),
    )
    return extract_code(response.text)


def read_any_csv(uploaded_file):
    """Read almost any CSV: handles different encodings and separators
    (comma, semicolon, tab, pipe)."""
    raw_bytes = uploaded_file.getvalue()

    # 1. Turn the bytes into text (utf-8 first, latin-1 accepts anything)
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1")

    # 2. Pick the separator that appears most often in the header line
    lines = text.splitlines()
    header = lines[0] if lines else ""
    separator = max([",", ";", "\t", "|"], key=header.count)
    if header.count(separator) == 0:
        separator = ","

    return pd.read_csv(io.StringIO(text), sep=separator)


def check_code_is_safe(code):
    """Returns an error message if the AI's code looks unsafe, otherwise None."""
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return f"SyntaxError: {error}"

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return "Blocked: imports are not allowed."
        if isinstance(node, ast.Name) and (
            node.id in BLOCKED_NAMES or node.id.startswith("__")
        ):
            return f"Blocked unsafe code: '{node.id}'"
        if isinstance(node, ast.Attribute) and (
            node.attr in BLOCKED_ATTRS or node.attr.startswith("__")
        ):
            return f"Blocked unsafe code: '{node.attr}'"
    return None


def format_metric(value):
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    if isinstance(value, (float, np.floating)):
        return f"{float(value):,.2f}"
    return str(value)


def run_code(code, df):
    """Run the AI's code on a copy of the data. Returns (namespace, error)."""
    problem = check_code_is_safe(code)
    if problem:
        return None, problem

    namespace = {"pd": pd, "np": np, "px": px, "go": go, "df": df.copy()}

    try:
        exec(code, namespace)
        return namespace, None
    except Exception as error:
        return None, f"{type(error).__name__}: {error}"


# ---------- Page ----------

st.set_page_config(page_title="AI Data Assistant", page_icon="📊", layout="wide")
st.title("📊 AI Data Assistant")
st.caption("Upload a CSV, tell the AI what to do, and get cleaned data, tables, charts or a dashboard.")

if "original_df" not in st.session_state:
    st.session_state.original_df = None
    st.session_state.df = None
    st.session_state.result = None
    st.session_state.file_name = None

api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    st.error("GOOGLE_API_KEY is missing. Add it to your .env file and restart the app.")
    st.stop()

client = genai.Client(api_key=api_key)

# ---------- Sidebar: buttons that run your existing project files ----------
with st.sidebar:
    st.header("Project tools")

    if not PROJECT_OK:
        st.warning(f"Project files could not be loaded: {IMPORT_ERROR}")
    else:
        st.caption("These buttons run your existing project files.")

        if st.button("▶ Run Snowflake pipeline (sales demo)"):
            logger.info("Pipeline started from the app.")
            with st.spinner("Running pipeline.py ..."):
                ok = pipeline.run_pipeline()
            st.session_state.project_output = {"kind": "pipeline", "ok": ok}

        if st.button("▶ Run transformation (sales demo)"):
            logger.info("Transformation started from the app.")
            with st.spinner("Running transform_data.py ..."):
                ok = td.transform_data()
            st.session_state.project_output = {"kind": "transform", "ok": ok}

output = st.session_state.get("project_output")
if output:
    with st.expander("Project output", expanded=True):
        if output["kind"] == "pipeline":
            if output["ok"]:
                st.success("Pipeline completed successfully.")
            else:
                st.error("Pipeline failed. Check logs/pipeline.log for details.")
            if pipeline.REPORT_FILE.exists():
                with open(pipeline.REPORT_FILE, "r", encoding="utf-8") as report_file:
                    st.json(json.load(report_file))
        else:
            if output["ok"]:
                st.success("Transformation completed successfully.")
                for title, path in [
                    ("Sales by city", td.CITY_OUTPUT_FILE),
                    ("Sales by product", td.PRODUCT_OUTPUT_FILE),
                    ("Sales summary", td.SUMMARY_OUTPUT_FILE),
                ]:
                    st.markdown(f"**{title}**")
                    st.dataframe(pd.read_csv(path), use_container_width=True)
            else:
                st.error("Transformation failed. Run the pipeline first, then check logs/pipeline.log.")

# Step 1: upload
uploaded = st.file_uploader("1. Upload your CSV file", type=["csv"])

if uploaded is not None and uploaded.name != st.session_state.file_name:
    try:
        data = read_any_csv(uploaded)
    except Exception as error:
        st.error(f"Could not read this CSV: {error}")
        st.stop()
    if data.empty or len(data.columns) == 0:
        st.error("This CSV has no data.")
        st.stop()
    data.columns = [str(column).strip() for column in data.columns]
    st.session_state.original_df = data
    st.session_state.df = data.copy()
    st.session_state.result = None
    st.session_state.file_name = uploaded.name

if st.session_state.df is None:
    st.info("Upload a CSV file to get started.")
    st.stop()

df = st.session_state.df

with st.expander("Current data preview", expanded=True):
    st.write(f"{len(df)} rows × {len(df.columns)} columns")
    st.dataframe(df.head(20), use_container_width=True)

# Step 2: instruction
instruction = st.text_area(
    "2. What should the AI do?",
    placeholder=(
        "Examples:\n"
        "• Remove duplicates, fix missing values and invalid prices, and give me the clean CSV\n"
        "• Show total sales by city as a table and a bar chart\n"
        "• Build a dashboard of this data"
    ),
    height=130,
)

col_run, col_reset = st.columns([1, 1])
run = col_run.button("Run", type="primary")

if col_reset.button("Reset to original data"):
    st.session_state.df = st.session_state.original_df.copy()
    st.session_state.result = None
    st.rerun()

# Step 3: run
if run:
    if not instruction.strip():
        st.warning("Please type an instruction first.")
    else:
        code, namespace, error = None, None, None
        logger.info("AI instruction: %s", instruction)

        with st.spinner("The AI is working on it..."):
            # Try up to 2 times: if the code fails, send the error back to the AI.
            for attempt in range(2):
                try:
                    code = ask_ai(client, df, instruction, code, error)
                except Exception as api_error:
                    error = f"AI request failed: {api_error}"
                    break

                namespace, error = run_code(code, df)
                if error is None:
                    break

        if error:
            logger.error("AI instruction failed: %s", error)
            st.session_state.result = {"error": error, "code": code}
        else:
            logger.info("AI instruction completed.")
            new_df = namespace.get("df")
            if isinstance(new_df, pd.DataFrame):
                st.session_state.df = new_df

            st.session_state.result = {
                "error": None,
                "code": code,
                "message": namespace.get("message", "Done."),
                "tables": namespace.get("tables", []),
                "figures": namespace.get("figures", []),
                "metrics": namespace.get("metrics", {}),
            }
        st.rerun()

# Step 4: show result (kept in session_state so it stays after downloads)
result = st.session_state.result

if result:
    st.divider()
    st.subheader("Result")

    if result["error"]:
        st.error(result["error"])
    else:
        st.success(result["message"])

        metrics = result["metrics"]
        if isinstance(metrics, dict) and metrics:
            items = list(metrics.items())
            for row_start in range(0, len(items), 4):   # 4 numbers per row
                row = items[row_start:row_start + 4]
                for column, (label, value) in zip(st.columns(len(row)), row):
                    column.metric(str(label), format_metric(value))

        tables = result["tables"]
        if not isinstance(tables, list):
            tables = [tables]
        for item in tables:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                title, table = item
            else:
                title, table = "Table", item
            if isinstance(table, pd.Series):
                table = table.reset_index()
            st.markdown(f"**{title}**")
            st.dataframe(table, use_container_width=True)

        figures = result["figures"]
        if not isinstance(figures, list):
            figures = [figures]
        for number, figure in enumerate(figures):
            if isinstance(figure, go.Figure):
                st.plotly_chart(figure, use_container_width=True, key=f"fig_{number}")

    with st.expander("Show the code the AI wrote"):
        st.code(result["code"] or "", language="python")

st.divider()
st.subheader("Save to Snowflake")

if not PROJECT_OK:
    st.caption("Not available: project files could not be loaded.")
elif all(column in st.session_state.df.columns for column in SALES_COLUMNS):
    st.caption(
        "Your data has the sales columns, so it can be checked with your pipeline's "
        "validate_data() and saved to the CLEAN_SALES_DATA table."
    )
    confirm = st.checkbox("I understand this replaces all rows in CLEAN_SALES_DATA")

    if st.button("Validate and save to Snowflake", disabled=not confirm):
        data = st.session_state.df.copy()
        errors = pipeline.validate_data(data)

        if errors:
            logger.error("Save to Snowflake blocked by validation: %s", errors)
            st.error("Validation failed, nothing was saved:")
            for message_text in errors:
                st.write("-", message_text)
        else:
            connection = None
            try:
                connection = pipeline.connect_to_snowflake()
                saved = pipeline.save_clean_data_to_snowflake(connection, data)

                pipeline.DATA_DIR.mkdir(parents=True, exist_ok=True)
                data.to_csv(pipeline.DATA_DIR / "ai_cleaned_data.csv", index=False)

                logger.info("Saved %d rows to Snowflake from the app.", saved)
                st.success(f"Saved {saved} rows to Snowflake and data/ai_cleaned_data.csv")
            except Exception as save_error:
                logger.exception("Saving to Snowflake failed.")
                st.error(f"{type(save_error).__name__}: {save_error}")
            finally:
                if connection is not None:
                    connection.close()
else:
    st.caption(
        "Available when the data has these columns: " + ", ".join(SALES_COLUMNS)
        + ". Ask the AI to clean the data and add total_sales = quantity * unit_price."
    )

st.divider()
st.download_button(
    "⬇️ Download current data as CSV",
    data=st.session_state.df.to_csv(index=False).encode("utf-8"),
    file_name="clean_data.csv",
    mime="text/csv",
)