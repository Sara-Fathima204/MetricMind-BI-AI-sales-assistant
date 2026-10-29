# RSG Sales Intelligence Assistant

A Python-based sales analytics application that allows users to ask business questions in natural language and receive data-backed answers. The application uses **FastAPI** for the backend, **SQLite** for structured storage, **pandas** for CSV processing, and **Streamlit** for the user interface.

## Features

- **CSV ingestion:** Imports sales transactions into SQLite.
- **Data validation:** Checks required columns, dates, numeric values, quantities, prices, discounts, and duplicate transaction IDs.
- **Data quarantine:** Stores rejected records and their rejection reasons in a separate `rejected_rows` table.
- **Natural-language analytics:** Supports approved questions about total revenue, top product, top category, monthly revenue trends, top region, month-to-month comparisons, channel revenue, transaction counts, total discounts, average revenue, and data quality.
- **Interactive dashboard:** Provides question prompts, supporting figures, tables, charts, formulas, filters, and API response details.
- **Auditable calculations:** Returns the analytical intent, supporting data, calculation formula, filters, and a query explanation.
- **Optional LLM integration:** Uses an LLM for question classification only. SQL execution remains in the deterministic backend.
- **Automated tests:** Includes a pytest test suite for core analytics, classification, data loading, and validation.

## Technology Stack

- Python
- FastAPI
- SQLite
- pandas
- Streamlit
- Requests
- pytest
- Optional OpenAI Python SDK for LLM-based intent classification

## Revenue Assumptions

1. Only transactions whose status is `completed` contribute to recognized revenue.
2. Line revenue is calculated as:

   `quantity × unit_price − discount_amount`

3. Invalid records are rejected during import. Validation includes missing required values, invalid dates or numbers, non-positive quantities or prices, negative discounts, discounts exceeding the gross line value, and duplicate transaction IDs.
4. For duplicate transaction IDs among otherwise valid rows, the first occurrence is retained and subsequent occurrences are rejected.
5. The transaction-count metric counts valid rows loaded into the `sales` table; it does not apply a completed-status filter.
6. The total-discount and average-revenue metrics use completed transactions only.

Confirm these assumptions with the evaluator if the assignment specifies a different revenue policy.

## Project Structure

```text
rsg_bi_assistant/
├── app.py              # FastAPI backend and analytics logic
├── ui.py               # Streamlit user interface
├── sample_sales.csv    # Demonstration dataset
├── sales.db            # SQLite database, created at runtime
├── requirements.txt    # Python dependencies
└── tests/
    └── test_app.py     # Automated tests
```

The database file is generated locally and should not be treated as the source dataset. The configured CSV is imported when the application starts.

## Requirements

- Python 3.10 or later
- pip
- A terminal such as Windows PowerShell
- The project CSV file

## Setup and Run on Windows

Open PowerShell in the project directory.

### 1. Create and activate a virtual environment

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Ensure `requirements.txt` includes the packages required by your implementation: `fastapi`, `uvicorn`, `pandas`, `streamlit`, `requests`, and `pytest`. If using LLM classification, install the `openai` package as well.

### 3. Configure the CSV, if needed

By default, the backend uses `sample_sales.csv` in the project directory.

To use a different dataset:

```powershell
$env:RSG_CSV_PATH="C:\full\path\to\RSG_sales.csv"
```

The CSV must contain the required fields:

`Transaction_ID, Order_date, Product, Category, Region, Sales_channel, Quantity, Unit_price, Discount_amount, Status`

Column names are normalized, and common aliases are supported.

### 4. Start the FastAPI backend

```powershell
python -c "from app import initialize_database; print(initialize_database())"
uvicorn app:app --reload
```

The initialization command displays the number of input, loaded, and rejected rows.

Keep this terminal running.

### 5. Start the Streamlit interface

Open a second PowerShell terminal in the project directory:

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run ui.py
```

Open the local Streamlit URL displayed in the terminal, normally:

http://localhost:8501

The API documentation is available at:

http://127.0.0.1:8000/docs

The backend health endpoint is:

http://127.0.0.1:8000/health

## Example Questions

Try these questions in the dashboard:

- What is the total revenue?
- Which product generated the highest revenue?
- Which category generated the highest revenue?
- What is the monthly revenue trend?
- Which region generated the highest sales?
- How did revenue change between 2026-01 and 2026-02?
- Show revenue by sales channel.
- What is the revenue from the Online channel?
- What is the revenue from the Retail channel?
- How many transactions are in the dataset?
- What is the total discount?
- What is the average revenue per completed transaction?
- Show data quality and rejected rows.

The application supports a defined set of analytics intents rather than unrestricted question answering. Questions that cannot be mapped to a supported intent receive an unsupported-question response.

## Optional LLM-Based Intent Classification

The application works without an API key and uses local intent-classification rules by default.

To enable optional OpenAI-based classification, set the environment variables in PowerShell before starting the backend:

```powershell
$env:OPENAI_API_KEY="your-key-here"
$env:OPENAI_MODEL="gpt-4o-mini"
uvicorn app:app --reload
```

Keep the API key private. Never commit credentials to source control.

The LLM is intended to classify a question into an approved intent, not generate executable SQL. Analytics are performed by fixed database queries. If LLM classification fails or returns an unsupported intent, the application falls back to local rules.

## Architecture

```text
User
 |
 v
Streamlit UI
 |
 | POST /ask
 v
FastAPI Backend
 |
 |-- Optional LLM intent classification
 |-- Local intent-classification fallback
 |
 v
Approved analytics intent
 |
 v
Fixed SQL aggregations
 |
 +--> SQLite: sales
 |
 +--> SQLite: rejected_rows
 |
 v
Structured JSON response
 |
 v
Answer, supporting data, formula,
filters, and query explanation
```

## API Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Checks backend availability |
| `POST /ask` | Processes a sales analytics question |
| `POST /reload` | Reloads data from the configured CSV |

The `/ask` endpoint accepts a JSON object containing a `question` string. Questions must contain between 3 and 500 characters.

## Testing

Run the automated test suite from the project directory:

```powershell
python -m pytest -v
```

The current recorded test run completed with **14 passed and 0 failed**. Two deprecation warnings relate to FastAPI's startup-event API; they did not cause the tests to fail.

Re-run the tests after any code changes and report the latest result when submitting.

## Limitations and Future Improvements

- The application supports predefined analytics intents and does not understand every possible business question.
- Natural-language interpretation may be ambiguous; verify that the selected intent and filters match the question.
- LLM-based interpretation depends on a valid API key, network access, and model availability.
- Channel-specific filtering should be verified in both local-rule and LLM-enabled modes.
- The current implementation reloads the configured CSV at startup, replacing the existing sales and rejected-row data.
- Future improvements could include exporting results to CSV or Excel, more flexible date filters, improved query classification, structured logging, CI with GitHub Actions, and deployment.
 


