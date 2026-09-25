"""Standalone script for streamlit.testing.v1.AppTest: exercises
components.sidebar_filters.render_sidebar_filters in isolation, against
fixed fake filter_options/date_bounds instead of a real database connection.

Not a page of the real app — only used by
dashboard/tests/test_sidebar_filters_state.py. AppTest.from_file executes
this file as the Streamlit script for each simulated run, the same way
app.py is the script for a real session.
"""
import sys
from pathlib import Path

_DASHBOARD_ROOT = Path(__file__).resolve().parents[2]
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

import streamlit as st

from components.sidebar_filters import render_sidebar_filters

FILTER_OPTIONS = {
    "form_types": ["8-K", "SC 13D", "SC TO-T"],
}
DATE_BOUNDS = {"min_date": "20240101", "max_date": "20260825"}

filters, sort_label, ascending = render_sidebar_filters(FILTER_OPTIONS, DATE_BOUNDS)

st.session_state["_test_filters"] = filters
st.session_state["_test_sort_label"] = sort_label
st.session_state["_test_ascending"] = ascending
