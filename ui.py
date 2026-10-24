import json
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Sales Intelligence Assistant", page_icon="📊", layout="wide")
st.title("📊 Sales Intelligence Assistant")
st.caption("Ask a business question. Every answer is backed by a database aggregation and shows its supporting figures.")

API = st.sidebar.text_input("Backend API URL", "http://127.0.0.1:8000")
st.sidebar.markdown("### Try asking")
examples = [
    "What is the total revenue?",
    "Which product generated the highest revenue?",
    "What is the monthly revenue trend?",
    "Which region generated the highest sales?",
    "How did revenue change between 2026-01 and 2026-02?",
    "Show revenue by sales channel",
    "Show data quality and rejected rows",
]
for example in examples:
    if st.sidebar.button(example, use_container_width=True):
        st.session_state["question"] = example

question = st.text_input("Ask a question about sales", key="question",
                         placeholder="e.g. Which product generated the highest revenue?")
if st.button("Analyze", type="primary") and question.strip():
    try:
        response = requests.post(f"{API.rstrip('/')}/ask", json={"question": question}, timeout=30)
        if response.ok:
            result = response.json()
            st.subheader("Answer")
            st.success(result["answer"])
            st.caption(f"Intent: {result.get('intent')} · Interpretation: {result.get('interpretation_source')}")
            st.subheader("Supporting figures")
            data = result.get("data")
            if isinstance(data, list) and data:
                df = pd.DataFrame(data)
                st.dataframe(df, use_container_width=True)
                if "revenue" in df.columns and "month" in df.columns:
                    st.line_chart(df.set_index("month")["revenue"])
                elif "revenue" in df.columns and len(df) > 1:
                    st.bar_chart(df.set_index(df.columns[0])["revenue"])
            elif isinstance(data, dict):
                st.json(data)
            else:
                st.write(data)
            st.subheader("How the answer was calculated")
            st.write("**Formula:**", result.get("formula") or "Not applicable")
            st.write("**Filters:**", ", ".join(result.get("filters", [])) or "None")
            if result.get("sql"):
                with st.expander("Query explanation / audit trail"):
                    st.code(result["sql"], language="sql")
            with st.expander("Full API response"):
                st.code(json.dumps(result, indent=2), language="json")
        else:
            st.error(f"API error ({response.status_code}): {response.text}")
    except requests.RequestException as exc:
        st.error(f"Could not reach the backend. Start FastAPI first. Details: {exc}")

st.divider()
st.caption("Revenue rule: completed transactions only; line revenue = quantity × unit price − discount. Invalid rows are excluded during import and reported.")
