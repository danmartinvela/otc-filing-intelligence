"""Renders the Explorador de Eventos filter panel in the sidebar.

Pure UI: takes the distinct option lists and date bounds the page already
fetched from the database, returns an EventFilters + the chosen sort — this
module never touches SQL.

Every filter persists in st.session_state under a durable key (_STATE keys
below) so navigating to another page and back restores the Explorador
unchanged. That durable key is deliberately never passed as a widget's own
`key=` — confirmed against a real two-page round trip (not just a same-page
rerun) that Streamlit prunes a widget's own key from session_state once that
widget isn't instantiated for a run, which a page switch always is. Binding
persistence directly to a widget's `key=` therefore quietly resets on the
very first return trip. Each widget instead gets its own throwaway internal
key (see _widget_key); the durable key is read to seed the widget's
`value=`/`index=`/`default=` before rendering it, and written back with the
widget's return value right after — plain session_state entries like that
are never touched by Streamlit's widget garbage collection, which is
exactly why _PAGE_KEY (see event_explorer.py) already survived page
switches before this file did anything special for it.

_widget_key also folds in a reset counter (see "Limpiar filtros" below):
confirmed against a real browser that some widgets (st.text_input in
particular) don't visually refresh from a new value= while their key stays
the same, even though the underlying session_state genuinely did change —
Streamlit/React treats a stable key as "the same component" and doesn't
force it to drop whatever the user last typed client-side. Bumping the
counter on every reset changes every widget's key at once, forcing a full
remount so the UI actually shows the reset values instead of only the
server-side state being correct.

No filter state is ever written anywhere other than st.session_state — nothing
touches SQLite, disk, or the browser's own storage, so a brand new session
(session_state empty) always starts from the defaults below.
"""
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

# Bumped once by "Limpiar filtros" to force every widget below to remount
# (see module docstring). Not itself a widget key, so it's as durable as
# every other key here — never touched by Streamlit's widget-state pruning.
RESET_COUNTER_KEY = "explorer_reset_counter"

# Must match event_explorer.py's own _PAGE_KEY — both modules key into the
# same st.session_state dict by this string, not by a shared Python import
# (same loose-string-key convention this dashboard already uses for e.g.
# "selected_filing" and "nav_pages").
_PAGE_KEY = "explorer_page"

_DEFAULT_SORT_LABEL = next(iter(SORT_OPTIONS))


def _widget_key(state_key: str, reset_counter: int) -> str:
    """The widget's own key — deliberately different from state_key (see
    module docstring). Its value is disposable; only state_key is durable.
    Takes reset_counter explicitly (rather than reading it from
    st.session_state itself) so this stays a pure function callers — tests
    included — can compute without needing a live Streamlit script context.
    """
    return f"_widget__{state_key}__{reset_counter}"


def _index_of(options: List[str], value, default_index: int = 0) -> int:
    try:
        return options.index(value)
    except ValueError:
        return default_index


def resolve_last_downloaded_date(date_bounds: Dict[str, str]) -> date:
    """The most recent date_filed among EVENT filings — the day this page
    should default to, since a whole-table "last downloaded" date could
    belong to a day with zero EVENT filings (all CONTEXT that day), which
    would default the Explorador to an empty table. Falls back to today
    when the database has no EVENT filings at all (date_bounds["max_date"]
    is None), per date_bounds already being computed with that fallback.
    """
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
    """Seed every durable filter key exactly once — never overwrites a key
    that's already there, which is what lets a value picked before a page
    switch survive the trip."""
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
