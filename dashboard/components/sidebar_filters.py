from datetime import date
from typing import Dict, List, Tuple

import streamlit as st

from services.filters import EventFilters, SORT_OPTIONS
from utils.formatting import date_to_yyyymmdd, yyyymmdd_to_date

DATE_MODE_DAY = "Día"
DATE_MODE_RANGE = "Rango"
DATE_MODES = [DATE_MODE_DAY, DATE_MODE_RANGE]

_DATE_MODE_KEY = "explorer_date_mode"
_SINGLE_DATE_KEY = "explorer_single_date"
_RANGE_KEY = "explorer_date_range"
_TICKER_KEY = "explorer_ticker"
_FORM_TYPES_KEY = "explorer_form_types"
_SORT_LABEL_KEY = "explorer_sort_label"
_ASCENDING_KEY = "explorer_ascending"

RESET_COUNTER_KEY = "explorer_reset_counter"

_PAGE_KEY = "explorer_page"

_DEFAULT_SORT_LABEL = next(iter(SORT_OPTIONS))


def _widget_key(state_key: str, reset_counter: int) -> str:
    return f"_widget__{state_key}__{reset_counter}"


def _index_of(options: List[str], value, default_index: int = 0) -> int:
    try:
        return options.index(value)
    except ValueError:
        return default_index


def resolve_last_downloaded_date(date_bounds: Dict[str, str]) -> date:
    return yyyymmdd_to_date(date_bounds.get("max_date"), fallback=date.today())


def _defaults(last_date: date) -> Dict:
    return {
        _DATE_MODE_KEY: DATE_MODE_DAY,
        _SINGLE_DATE_KEY: last_date,
        _RANGE_KEY: (last_date, last_date),
        _TICKER_KEY: "",
        _FORM_TYPES_KEY: [],
        _SORT_LABEL_KEY: _DEFAULT_SORT_LABEL,
        _ASCENDING_KEY: False,
    }


def _ensure_state(last_date: date) -> None:
    if RESET_COUNTER_KEY not in st.session_state:
        st.session_state[RESET_COUNTER_KEY] = 0
    for key, value in _defaults(last_date).items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_sidebar_filters(
    filter_options: Dict[str, List[str]], date_bounds: Dict[str, str]
) -> Tuple[EventFilters, str, bool]:
    min_date = yyyymmdd_to_date(date_bounds.get("min_date"), fallback=date(2020, 1, 1))
    max_date = yyyymmdd_to_date(date_bounds.get("max_date"), fallback=date.today())
    last_downloaded_date = resolve_last_downloaded_date(date_bounds)

    _ensure_state(last_downloaded_date)
    reset_counter = st.session_state[RESET_COUNTER_KEY]

    def wkey(state_key: str) -> str:
        return _widget_key(state_key, reset_counter)

    st.sidebar.markdown('<div class="filter-panel-title">Filtros</div>', unsafe_allow_html=True)

    date_mode = st.sidebar.radio(
        "Filtro temporal", DATE_MODES,
        index=_index_of(DATE_MODES, st.session_state[_DATE_MODE_KEY]),
        key=wkey(_DATE_MODE_KEY), horizontal=True,
    )
    st.session_state[_DATE_MODE_KEY] = date_mode

    if date_mode == DATE_MODE_RANGE:
        date_range = st.sidebar.date_input(
            "Rango de fechas", value=st.session_state[_RANGE_KEY],
            min_value=min_date, max_value=max_date, key=wkey(_RANGE_KEY),
        )
        date_from, date_to = date_range if len(date_range) == 2 else (date_range[0], date_range[0])
        st.session_state[_RANGE_KEY] = (date_from, date_to)
    else:
        selected_date = st.sidebar.date_input(
            "Fecha", value=st.session_state[_SINGLE_DATE_KEY],
            min_value=min_date, max_value=max_date, key=wkey(_SINGLE_DATE_KEY),
        )
        st.session_state[_SINGLE_DATE_KEY] = selected_date
        date_from = date_to = selected_date

    ticker = st.sidebar.text_input(
        "Ticker", value=st.session_state[_TICKER_KEY],
        placeholder="AAPL", key=wkey(_TICKER_KEY),
    )
    st.session_state[_TICKER_KEY] = ticker

    form_type_options = filter_options.get("form_types", [])
    form_types = st.sidebar.multiselect(
        "Formulario", options=form_type_options,
        default=[v for v in st.session_state[_FORM_TYPES_KEY] if v in form_type_options],
        key=wkey(_FORM_TYPES_KEY),
    )
    st.session_state[_FORM_TYPES_KEY] = form_types

    st.sidebar.markdown('<div class="filter-panel-title filter-panel-title--section">Orden</div>', unsafe_allow_html=True)
    sort_options_list = list(SORT_OPTIONS.keys())
    sort_label = st.sidebar.selectbox(
        "Ordenar por", options=sort_options_list,
        index=_index_of(sort_options_list, st.session_state[_SORT_LABEL_KEY]),
        key=wkey(_SORT_LABEL_KEY),
    )
    st.session_state[_SORT_LABEL_KEY] = sort_label

    ascending = st.sidebar.toggle(
        "Ascendente", value=st.session_state[_ASCENDING_KEY], key=wkey(_ASCENDING_KEY),
    )
    st.session_state[_ASCENDING_KEY] = ascending

    if st.sidebar.button("Limpiar filtros", width="stretch"):
        for key, value in _defaults(last_downloaded_date).items():
            st.session_state[key] = value
        st.session_state[_PAGE_KEY] = 1
        st.session_state[RESET_COUNTER_KEY] = reset_counter + 1
        st.rerun()

    filters = EventFilters(
        date_from=date_to_yyyymmdd(date_from),
        date_to=date_to_yyyymmdd(date_to),
        ticker=ticker,
        form_types=form_types,
    )
    return filters, sort_label, ascending
