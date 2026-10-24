
from pathlib import Path

import pandas as pd
import pytest
import app as service


@pytest.fixture()
def test_db(tmp_path, monkeypatch):
    # Load the actual CSV file from the project directory
    csv = Path(service.__file__).resolve().parent / "sample_sales.csv"

    if not csv.exists():
        pytest.fail(f"Dataset not found: {csv}")

    # Verify that the complete CSV can be read
   
    df = pd.read_csv(csv, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()

    test_csv = tmp_path / "sales.csv"
    df.to_csv(test_csv, index=False)


    monkeypatch.setattr(service, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(service, "CSV_PATH", test_csv)

    service.initialize_database(test_csv)

    return service


def test_total_revenue(test_db):
    result = test_db.run_analytics({"intent": "total_revenue"})
    assert "data" in result


def test_top_product(test_db):
    result = test_db.run_analytics({"intent": "top_product"})
    assert "data" in result


def test_monthly_trend(test_db):
    result = test_db.run_analytics({"intent": "monthly_trend"})
    assert "data" in result


def test_invalid_question_is_unsupported(test_db):
    intent = test_db.classify_question("Tell me a joke")
    assert intent["intent"] == "unsupported"
    assert "couldn't map" in test_db.run_analytics(intent)["answer"]


def test_edge_case_month_without_sales(test_db):
    result = test_db.run_analytics({
        "intent": "compare_months",
        "month_a": "2026-01",
        "month_b": "2026-12"
    })

    assert result["data"][1]["revenue"] == 0.0


def test_bad_rows_are_quarantined(test_db):
    with test_db.get_conn() as conn:
        rejected = conn.execute(
            "SELECT COUNT(*) FROM rejected_rows"
        ).fetchone()[0]

    # The real CSV has at least one incomplete-price record:
    # TXN0051 has a missing Unit_price.
    assert rejected >= 1


def test_question_classifier(test_db):
    assert (
        test_db.classify_question(
            "Which region generated the highest sales?"
        )["intent"]
        == "top_region"
    )



def test_complete_csv_loaded(test_db):
    with test_db.get_conn() as conn:
        loaded = conn.execute(
            "SELECT COUNT(*) FROM sales"
        ).fetchone()[0]

        rejected = conn.execute(
            "SELECT COUNT(*) FROM rejected_rows"
        ).fetchone()[0]

    # The supplied CSV contains 51 rows.
    assert loaded + rejected == 51
def test_transaction_count(test_db):
    result = test_db.run_analytics(
        {"intent": "transaction_count"}
    )

    assert result["data"]["transaction_count"] == 49


def test_total_discount(test_db):
    result = test_db.run_analytics(
        {"intent": "total_discount"}
    )

    assert result["data"]["total_discount"] == 4500.0


def test_average_revenue(test_db):
    result = test_db.run_analytics(
        {"intent": "average_revenue"}
    )

    assert result["data"]["completed_transactions"] == 48
    assert result["data"]["average_revenue"] == 29983.33


def test_category_question_classifier(test_db):
    result = test_db.classify_question(
        "Which category has the highest revenue?"
    )

    assert result["intent"] == "top_category"


def test_online_channel_question_classifier(test_db):
    result = test_db.classify_question(
        "Revenue from the Online channel"
    )

    assert result["intent"] == "revenue_by_channel"


def test_rejected_record_count(test_db):
    with test_db.get_conn() as conn:
        rejected = conn.execute(
            "SELECT COUNT(*) FROM rejected_rows"
        ).fetchone()[0]

    assert rejected == 2
