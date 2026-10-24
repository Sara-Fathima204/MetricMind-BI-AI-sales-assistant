from __future__ import annotations
import os, re, sqlite3
from pathlib import Path
from typing import Any
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("RSG_DB_PATH", BASE_DIR / "sales.db"))
CSV_PATH = Path(os.getenv("RSG_CSV_PATH", BASE_DIR / "sample_sales.csv"))
app = FastAPI(title="RSG Sales Intelligence Assistant", version="1.0.0")

REQUIRED = ["transaction_id", "order_date", "product", "category", "region",
            "sales_channel", "quantity", "unit_price", "discount_amount", "status"]

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(s).strip().lower()).strip("_")
    df = df.copy()
    df.columns = [norm(c) for c in df.columns]
    aliases = {
        "transaction_id": ["transaction_id", "transactionid", "txn_id"],
        "order_date": ["order_date", "orderdate", "date"],
        "product": ["product", "product_name"],
        "category": ["category"],
        "region": ["region"],
        "sales_channel": ["sales_channel", "channel"],
        "quantity": ["quantity", "qty"],
        "unit_price": ["unit_price", "unitprice", "price"],
        "discount_amount": ["discount_amount", "discount", "discount_amt"],
        "status": ["status", "order_status"],
    }
    rename = {}
    for target, choices in aliases.items():
        for c in choices:
            if c in df.columns:
                rename[c] = target
                break
    return df.rename(columns=rename)

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_database(csv_path: str | Path = CSV_PATH) -> dict[str, Any]:
    """Load a CSV, normalize columns, validate rows, and store valid rows in SQLite."""
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    raw = normalize_columns(
    pd.read_csv(path, encoding="utf-8-sig")
    )
    missing_columns = [c for c in REQUIRED if c not in raw.columns]
    if missing_columns:
        raise ValueError("CSV is missing required columns: " + ", ".join(missing_columns))

    def parse_date(value):
        if pd.isna(value):
            return pd.NaT
        text = str(value).strip()
        # Respect ISO dates (YYYY-MM-DD) and the assignment's day-first dates (DD-MM-YYYY).
        if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$", text):
            return pd.to_datetime(text, errors="coerce", yearfirst=True)
        return pd.to_datetime(text, errors="coerce", dayfirst=True)
    raw["order_date"] = raw["order_date"].map(parse_date)
    for c in ["quantity", "unit_price", "discount_amount"]:
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    for c in ["transaction_id", "product", "category", "region", "sales_channel", "status"]:
        raw[c] = raw[c].astype("string").str.strip()

    reasons = []
    valid = []
    for _, row in raw.iterrows():
        reason = None
        if pd.isna(row["transaction_id"]) or not str(row["transaction_id"]).strip():
            reason = "missing transaction id"
        elif pd.isna(row["order_date"]):
            reason = "invalid or missing date"
        elif any(pd.isna(row[c]) for c in ["product", "category", "region", "sales_channel", "status"]):
            reason = "missing text field"
        elif any(pd.isna(row[c]) for c in ["quantity", "unit_price", "discount_amount"]):
            reason = "missing or invalid numeric value"
        elif row["quantity"] <= 0:
            reason = "quantity must be positive"
        elif row["unit_price"] <= 0:
            reason = "unit price must be positive"
        elif row["discount_amount"] < 0:
            reason = "discount cannot be negative"
        elif row["discount_amount"] > row["quantity"] * row["unit_price"]:
            reason = "discount exceeds line value"
        if reason:
            reasons.append({"transaction_id": str(row.get("transaction_id", "")), "reason": reason})
        else:
            valid.append({
                "transaction_id": str(row["transaction_id"]),
                "order_date": row["order_date"].strftime("%Y-%m-%d"),
                "product": str(row["product"]), "category": str(row["category"]),
                "region": str(row["region"]), "sales_channel": str(row["sales_channel"]),
                "quantity": int(row["quantity"]), "unit_price": float(row["unit_price"]),
                "discount_amount": float(row["discount_amount"]), "status": str(row["status"]).lower()
            })

    # Duplicate transaction IDs: keep first occurrence and quarantine later duplicates.
    unique = []
    seen = set()
    for row in valid:
        if row["transaction_id"] in seen:
            reasons.append({"transaction_id": row["transaction_id"], "reason": "duplicate transaction id"})
        else:
            seen.add(row["transaction_id"])
            unique.append(row)

    with get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS sales (
            transaction_id TEXT PRIMARY KEY, order_date TEXT NOT NULL, product TEXT NOT NULL,
            category TEXT NOT NULL, region TEXT NOT NULL, sales_channel TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0), unit_price REAL NOT NULL CHECK(unit_price > 0),
            discount_amount REAL NOT NULL CHECK(discount_amount >= 0), status TEXT NOT NULL
        )""")
        conn.execute("DELETE FROM sales")
        conn.executemany("""INSERT INTO sales VALUES
            (:transaction_id,:order_date,:product,:category,:region,:sales_channel,
             :quantity,:unit_price,:discount_amount,:status)""", unique)
        conn.execute("""CREATE TABLE IF NOT EXISTS rejected_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id TEXT, reason TEXT NOT NULL)""")
        conn.execute("DELETE FROM rejected_rows")
        conn.executemany("INSERT INTO rejected_rows(transaction_id,reason) VALUES (?,?)",
                         [(r["transaction_id"], r["reason"]) for r in reasons])
    return {"input_rows": len(raw), "loaded_rows": len(unique), "rejected_rows": len(reasons),
            "rejection_reasons": reasons}

def ensure_loaded():
    with get_conn() as conn:
        exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sales'").fetchone()
    if not exists:
        initialize_database()

def ask_llm_for_intent(question: str) -> dict | None:
    """Optional LLM: return a small JSON intent, never executable SQL."""
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": (
                    "Classify a sales analytics question into JSON only. Never generate SQL. "
                    "Allowed intents: total_revenue, top_product, top_category, monthly_trend, "
                    "top_region, compare_months, revenue_by_channel, data_quality, "
                    "transaction_count, total_discount, average_revenue, unsupported. "
                    "Return keys intent, month_a, month_b. Month values should be YYYY-MM or null. "
                    "Only classify; do not answer the question."
                )},
                {"role": "user", "content": question}
            ])
        import json
        data = json.loads(response.choices[0].message.content)
        if data.get("intent") not in {
                    "total_revenue",
                    "top_product",
                    "top_category",
                    "monthly_trend",
                    "top_region",
                    "compare_months",
                    "revenue_by_channel",
                    "data_quality",
                    "transaction_count",
                    "total_discount",
                    "average_revenue",
                    "unsupported",
                         }:
            return None
        return data
    except Exception:
        # LLM outage or malformed response must not take down the deterministic app.
        return None

def classify_question(question: str) -> dict:
    q = question.lower().strip()
    months = re.findall(r"\b(20\d{2})[-/ ](0?[1-9]|1[0-2])\b", q)
        # Count rejected records
    if any(p in q for p in [
        "how many records were rejected",
        "number of rejected records",
        "rejected records",
        "how many rejected rows",
        "number of rejected rows"
    ]):
        return {"intent": "data_quality"}

    # Total discount
    if any(p in q for p in [
        "total discount",
        "total discounts",
        "discount given",
        "discounts given",
        "discount amount"
    ]):
        return {"intent": "total_discount"}

    # Average revenue per completed transaction
    if any(p in q for p in [
        "average revenue per completed transaction",
        "average revenue per transaction",
        "average revenue per order",
        "average transaction revenue",
        "revenue per completed transaction"
    ]):
        return {"intent": "average_revenue"}
    if any(phrase in q for phrase in [
    "how many transactions",
    "number of transactions",
    "transaction count",
    "count transactions",
    "how many valid orders",
    "number of valid orders",
    "count of completed orders",
    "how many completed orders",
    "how many transactions are made"
    ]):
        return {"intent": "transaction_count"}
    if any(x in q for x in ["data quality", "invalid rows", "missing data", "rejected rows"]):
        return {"intent": "data_quality"}
    if any(x in q for x in [
    "compare",
    "change between",
    "difference between",
    "growth between",
    "compared with",
    "compared to",
   ]):
        if len(months) >= 2:
            return {"intent": "compare_months", "month_a": f"{months[0][0]}-{int(months[0][1]):02d}",
                    "month_b": f"{months[1][0]}-{int(months[1][1]):02d}"}
        return {"intent": "compare_months", "month_a": None, "month_b": None}
    
    if "channel" in q or "online" in q or "retail" in q:
        channel = None

        if "online" in q:
            channel = "Online"
        elif "retail" in q:
            channel = "Retail"

        return {
            "intent": "revenue_by_channel",
            "channel": channel
        }

    if "monthly" in q or "trend" in q or "each month" in q or "per month" in q:
        return {"intent": "monthly_trend"}
    if "region" in q or "area" in q:
        return {"intent": "top_region"}
    if "categor" in q and any(x in q for x in [
    "highest", "top", "best", "most", "maximum"
   ]):
        return {"intent": "top_category"}
    if "product" in q or "item" in q:
        return {"intent": "top_product"}
    if "total" in q or "overall" in q or "revenue" in q or "sales" in q:
        return {"intent": "total_revenue"}
    return {"intent": "unsupported"}

def run_analytics(intent: dict) -> dict:
    ensure_loaded()
    name = intent.get("intent")
    
    if name == "top_category":
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT
                    category,
                    ROUND(SUM(quantity * unit_price - discount_amount), 2) AS revenue
                FROM sales
                WHERE lower(status) = 'completed'
                GROUP BY category
                ORDER BY revenue DESC
                LIMIT 1
            """).fetchall()

        if not rows:
            return empty_result(name)

        row = dict(rows[0])

        return {
            "intent": name,
            "answer": (
                f"{row['category']} generated the highest recognized "
                f"revenue: ₹{row['revenue']:,.2f}."
            ),
            "data": row,
            "formula": "Sum of line revenue grouped by category",
            "filters": ["status = completed"],
            "sql": "GROUP BY category ORDER BY revenue DESC LIMIT 1"
        }

    if name == "transaction_count":
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS transaction_count FROM sales"
            ).fetchone()
        count = row["transaction_count"]

        return {
            "intent": name,
            "answer": f"There are {count} valid transactions in the dataset.",
            "data": {"transaction_count": count},
            "formula": "Count of valid transactions loaded into the sales table",
            "filters": [],
            "sql": "SELECT COUNT(*) AS transaction_count FROM sales"
        }
    
    if name == "total_discount":
        with get_conn() as conn:
            row = conn.execute("""
                SELECT ROUND(
                    COALESCE(SUM(discount_amount), 0), 2
                ) AS total_discount
                FROM sales
                WHERE lower(status) = 'completed'
            """).fetchone()

        total_discount = float(row["total_discount"] or 0)

        return {
            "intent": name,
            "answer": (
                f"Total discounts on completed transactions: "
                f"₹{total_discount:,.2f}."
            ),
            "data": {
                "total_discount": total_discount
            },
            "formula": "Sum of discount_amount",
            "filters": ["status = completed"],
            "sql": (
                "SUM(discount_amount) "
                "WHERE lower(status) = 'completed'"
            )
        }

    if name == "average_revenue":
        with get_conn() as conn:
            row = conn.execute("""
                SELECT
                    ROUND(
                        COALESCE(
                            SUM(quantity * unit_price - discount_amount) * 1.0
                            / NULLIF(COUNT(*), 0),
                            0
                        ), 2
                    ) AS average_revenue,
                    COUNT(*) AS completed_transactions,
                    ROUND(
                        COALESCE(SUM(quantity * unit_price - discount_amount), 0), 2
                    ) AS total_revenue
                FROM sales
                WHERE lower(status) = 'completed'
            """).fetchone()

        average = row["average_revenue"]
        count = row["completed_transactions"]

        return {
            "intent": name,
            "answer": (
                f"Average revenue per completed transaction is ₹{average:,.2f}, "
                f"calculated across {count} completed transactions."
            ),
            "data": dict(row),
            "formula": "Total recognized revenue / number of completed transactions",
            "filters": ["status = completed", "valid transactions only"],
            "sql": "Completed revenue divided by completed transaction count"
        }
    # All SQL is fixed/allow-listed. The LLM never supplies executable SQL.
    with get_conn() as conn:
        if name == "total_revenue":
            row = conn.execute("""SELECT ROUND(COALESCE(SUM(quantity*unit_price-discount_amount),0),2) revenue,
                COUNT(*) orders FROM sales WHERE lower(status)='completed'""").fetchone()
            return {"intent": name, "answer": f"Total recognized revenue is ₹{row['revenue']:,.2f}.",
                    "data": dict(row), "formula": "SUM(quantity × unit_price − discount_amount)",
                    "filters": ["status = completed"], "sql": "SELECT SUM(quantity*unit_price-discount_amount), COUNT(*) FROM sales WHERE lower(status)='completed'"}
        if name == "top_product":
            rows = conn.execute("""SELECT product, ROUND(SUM(quantity*unit_price-discount_amount),2) revenue
                FROM sales WHERE lower(status)='completed' GROUP BY product ORDER BY revenue DESC LIMIT 1""").fetchall()
            if not rows: return empty_result(name)
            r = dict(rows[0])
            return {"intent": name, "answer": f"{r['product']} generated the highest recognized revenue: ₹{r['revenue']:,.2f}.",
                    "data": r, "formula": "Sum of line revenue grouped by product",
                    "filters": ["status = completed"], "sql": "GROUP BY product ORDER BY revenue DESC LIMIT 1"}
        if name == "monthly_trend":
            rows = [dict(r) for r in conn.execute("""SELECT substr(order_date,1,7) month,
                ROUND(SUM(quantity*unit_price-discount_amount),2) revenue
                FROM sales WHERE lower(status)='completed' GROUP BY month ORDER BY month""").fetchall()]
            return {"intent": name, "answer": "Monthly recognized revenue trend is shown in the supporting figures.",
                    "data": rows, "formula": "Sum of line revenue grouped by YYYY-MM",
                    "filters": ["status = completed"], "sql": "GROUP BY substr(order_date,1,7) ORDER BY month"}
        if name == "top_region":
            rows = conn.execute("""SELECT region, ROUND(SUM(quantity*unit_price-discount_amount),2) revenue
                FROM sales WHERE lower(status)='completed' GROUP BY region ORDER BY revenue DESC LIMIT 1""").fetchall()
            if not rows: return empty_result(name)
            r = dict(rows[0])
            return {"intent": name, "answer": f"{r['region']} generated the highest recognized revenue: ₹{r['revenue']:,.2f}.",
                    "data": r, "formula": "Sum of line revenue grouped by region",
                    "filters": ["status = completed"], "sql": "GROUP BY region ORDER BY revenue DESC LIMIT 1"}
        if name == "compare_months":
            a, b = intent.get("month_a"), intent.get("month_b")
            if not a or not b:
                return {"intent": name, "answer": "Please specify two months, for example: 'How did revenue change between 2026-01 and 2026-02?'",
                        "data": [], "formula": "difference = month B revenue − month A revenue",
                        "filters": [], "sql": None}
            vals = {}
            for month in [a,b]:
                row = conn.execute("""SELECT ROUND(COALESCE(SUM(quantity*unit_price-discount_amount),0),2) revenue
                    FROM sales WHERE lower(status)='completed' AND substr(order_date,1,7)=?""", (month,)).fetchone()
                vals[month] = float(row["revenue"])
            diff = round(vals[b]-vals[a],2)
            pct = round((diff / vals[a] * 100),2) if vals[a] else None
            direction = "increased" if diff > 0 else "decreased" if diff < 0 else "did not change"
            pct_text = f" ({pct:+.2f}%)" if pct is not None else " (percentage change unavailable because the first month is zero)"
            return {"intent": name, "answer": f"Revenue {direction} by ₹{abs(diff):,.2f}{pct_text} from {a} to {b}.",
                    "data": [{"month": a, "revenue": vals[a]}, {"month": b, "revenue": vals[b]},
                             {"difference": diff, "percentage_change": pct}],
                    "formula": "difference = month B − month A; percentage = difference / month A × 100",
                    "filters": ["status = completed", f"months = {a}, {b}"], "sql": "Parameterized month filters; aggregate query"}
        
        if name == "revenue_by_channel":
            channel = intent.get("channel")

            if channel:
                rows = conn.execute("""
                    SELECT sales_channel,
                        ROUND(SUM(quantity * unit_price - discount_amount), 2)
                            AS revenue
                    FROM sales
                    WHERE lower(status) = 'completed'
                      AND lower(sales_channel) = lower(?)
                    GROUP BY sales_channel
                """, (channel,)).fetchall()

                if not rows:
                    return {
                        "intent": name,
                        "answer": f"No completed sales were found for {channel}.",
                        "data": [],
                        "formula": "Sum of line revenue for the requested channel",
                        "filters": [
                            "status = completed",
                            f"sales_channel = {channel}"
                        ],
                        "sql": "Parameterized sales channel filter"
                    }

                row = dict(rows[0])
                return {
                    "intent": name,
                    "answer": (
                        f"Recognized revenue from the {row['sales_channel']} "
                        f"channel is ₹{row['revenue']:,.2f}."
                    ),
                    "data": row,
                    "formula": "Sum of line revenue for the requested channel",
                    "filters": [
                        "status = completed",
                        f"sales_channel = {row['sales_channel']}"
                    ],
                    "sql": "Parameterized sales channel filter"
                }

            rows = [
                dict(r) for r in conn.execute("""
                    SELECT sales_channel,
                        ROUND(SUM(quantity * unit_price - discount_amount), 2)
                            AS revenue
                    FROM sales
                    WHERE lower(status) = 'completed'
                    GROUP BY sales_channel
                    ORDER BY revenue DESC
                """).fetchall()
            ]

            return {
                "intent": name,
                "answer": "Revenue by sales channel is shown in the supporting figures.",
                "data": rows,
                "formula": "Sum of line revenue grouped by sales channel",
                "filters": ["status = completed"],
                "sql": "GROUP BY sales_channel ORDER BY revenue DESC"
            }

        if name == "data_quality":
            row = conn.execute("SELECT COUNT(*) n FROM rejected_rows").fetchone()
            total = conn.execute("SELECT COUNT(*) n FROM sales").fetchone()
            reasons = [dict(r) for r in conn.execute("SELECT reason, COUNT(*) count FROM rejected_rows GROUP BY reason ORDER BY count DESC").fetchall()]
            return {"intent": name, "answer": (
    f"Loaded {total['n']} valid records and rejected {row['n']} records. "
    + (
        "; ".join(
            f"{r['transaction_id'] or 'Unknown ID'}: {r['reason']}"
            for r in conn.execute(
                "SELECT transaction_id, reason FROM rejected_rows ORDER BY id"
            ).fetchall()
        )
        if row["n"] > 0
        else "No records were rejected."
    )
),"data": {"loaded_rows": total["n"], "rejected_rows": row["n"], "reasons": reasons},
                    "formula": "Validation report from import process", "filters": [], "sql": "SELECT reason, COUNT(*) FROM rejected_rows GROUP BY reason"}
    return {"intent": "unsupported", "answer": "I couldn't map that question to a supported sales analysis. Try total revenue, top product, monthly trend, top region, compare two months, revenue by channel, or data quality.",
            "data": [], "formula": None, "filters": [], "sql": None}

def empty_result(name):
    return {"intent": name, "answer": "No matching completed sales records were found.",
            "data": [], "formula": None, "filters": ["status = completed"], "sql": None}

class QuestionRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@app.on_event("startup")
def startup():
    # Always load the latest complete CSV into the configured database.
    initialize_database(CSV_PATH)


@app.get("/health")
def health():
    return {"status": "ok", "service": "RSG Sales Intelligence Assistant"}


@app.post("/ask")
def ask(req: QuestionRequest):
    question = req.question.strip()

    if not question:
        raise HTTPException(
            status_code=422,
            detail="Question cannot be blank."
        )

    supported_intents = {
        "total_revenue",
        "top_product",
        "top_category",
        "monthly_trend",
        "top_region",
        "compare_months",
        "revenue_by_channel",
        "data_quality",
        "transaction_count",
        "total_discount",
        "average_revenue",
    }

    llm_intent = ask_llm_for_intent(question)

    if llm_intent and llm_intent.get("intent") in supported_intents:
        intent = llm_intent
        source = "LLM intent classification"
    else:
        intent = classify_question(question)
        source = (
            "local rules (LLM fallback)"
            if os.getenv("OPENAI_API_KEY")
            else "local intent rules"
        )

    result = run_analytics(intent)
    result["question"] = question
    result["interpretation_source"] = source

    return result
    

@app.post("/reload")
def reload_data():
    """Reload from configured CSV. Intended for local demo/admin use."""
    try:
        return initialize_database(CSV_PATH)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
