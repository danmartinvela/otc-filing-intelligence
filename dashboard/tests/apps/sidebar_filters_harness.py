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
