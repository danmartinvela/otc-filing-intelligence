"""Tests for the Explorador de Eventos filter panel:
  - resolve_last_downloaded_date: pure calculation, no Streamlit runtime.
  - Everything else: driven through streamlit.testing.v1.AppTest against the
    sidebar_filters_harness.py script.

IMPORTANT caveat about what AppTest can and can't prove here: AppTest only
ever re-runs the *same* script — it has no notion of a multi-page app, so it
cannot exercise an actual page-to-page round trip. Two real bugs this
component had to be fixed for only reproduced against a genuine two-page
trip / DOM inspection in a live browser (manual Playwright runs against
`streamlit run app.py`, not AppTest):
  1. Streamlit prunes a widget's own `key=` from session_state the moment
     that widget isn't instantiated in a run — which a real page switch
     always causes, but a same-page rerun never does.
  2. Some widgets (st.text_input in particular) don't visually refresh from
     a new value= while their key stays the same, even once the underlying
     session_state is genuinely updated — confirmed by reading the DOM
     after a full page reload, which showed the correct reset value even
     while the un-reloaded page still displayed the stale one.
These tests instead verify what AppTest *can* prove: widgets read from and
write to the correct *durable* session_state keys (never the disposable
per-widget keys — see _widget_key in sidebar_filters.py), defaults are
seeded only once, and "Limpiar filtros" resets every durable key plus bumps
the reset counter. That, plus the fact that _PAGE_KEY (a plain, non-widget
session_state entry) already reliably survived page switches before this
feature touched anything, is what the durable-key design in
sidebar_filters.py relies on for actual cross-page persistence — verified
end-to-end with a real browser, not by this file.
"""
from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

from components.sidebar_filters import RESET_COUNTER_KEY, _widget_key, resolve_last_downloaded_date

_HARNESS = str(Path(__file__).resolve().parent / "apps" / "sidebar_filters_harness.py")


def _run() -> AppTest:
    at = AppTest.from_file(_HARNESS)
    at.run()
    assert not at.exception
    return at


def _w(at: AppTest, state_key: str) -> str:
    """The AppTest widget locator key for a given durable state key, at the
    session's current reset counter (see sidebar_filters._widget_key)."""
    return _widget_key(state_key, at.session_state[RESET_COUNTER_KEY])


# ── resolve_last_downloaded_date ─────────────────────────────────────────────


def test_resolve_last_downloaded_date_uses_max_event_date():
    assert resolve_last_downloaded_date({"min_date": "20240101", "max_date": "20260825"}) == date(2026, 8, 25)


def test_resolve_last_downloaded_date_falls_back_to_today_when_no_filings():
    assert resolve_last_downloaded_date({"min_date": None, "max_date": None}) == date.today()


def test_resolve_last_downloaded_date_falls_back_on_missing_keys():
    assert resolve_last_downloaded_date({}) == date.today()


# ── defaults on first render ─────────────────────────────────────────────────


def test_defaults_on_first_render():
    at = _run()
    assert at.session_state["explorer_date_mode"] == "Día"
    assert at.session_state["explorer_single_date"] == date(2026, 8, 25)  # harness's fake max_date
    assert at.session_state["explorer_ticker"] == ""
    assert at.session_state["explorer_form_types"] == []
    assert at.session_state["explorer_sort_label"] == "Fecha"
    assert at.session_state["explorer_ascending"] is False


def test_day_mode_shows_a_single_date_input():
    at = _run()
    assert len(at.date_input) == 1


def test_default_filters_select_the_single_default_day():
    at = _run()
    filters = at.session_state["_test_filters"]
    assert filters.date_from == filters.date_to == "20260825"


# ── widgets read/write the durable key, not their own disposable key ────────
#
# This is what the fix actually depends on: as long as every widget below is
# seeded from, and writes back to, the *durable* key (never trusting its own
# `key=` to carry the value across a run where it isn't instantiated), the
# real page-to-page persistence follows for free — see the module docstring.


def test_ticker_change_persists_across_unrelated_rerun():
    at = _run()
    at.text_input(key=_w(at, "explorer_ticker")).set_value("ABCH")
    at.run()
    assert at.session_state["explorer_ticker"] == "ABCH"

    # A rerun that only touches a different widget must not reset this one.
    at.multiselect(key=_w(at, "explorer_form_types")).select("8-K")
    at.run()
    assert at.session_state["explorer_ticker"] == "ABCH"
    assert at.session_state["explorer_form_types"] == ["8-K"]


def test_selected_single_date_persists_across_unrelated_rerun():
    at = _run()
    chosen = date(2026, 1, 5)
    at.date_input(key=_w(at, "explorer_single_date")).set_value(chosen)
    at.run()
    assert at.session_state["explorer_single_date"] == chosen

    at.multiselect(key=_w(at, "explorer_form_types")).select("8-K")
    at.run()
    assert at.session_state["explorer_single_date"] == chosen
    assert at.session_state["explorer_form_types"] == ["8-K"]


def test_all_filter_types_persist_together():
    at = _run()
    at.text_input(key=_w(at, "explorer_ticker")).set_value("ABCH")
    at.multiselect(key=_w(at, "explorer_form_types")).select("8-K")
    at.selectbox(key=_w(at, "explorer_sort_label")).select("Importance score")
    at.toggle(key=_w(at, "explorer_ascending")).set_value(True)
    at.run()

    # A further, unrelated rerun must leave every one of these exactly as set.
    at.run()

    assert at.session_state["explorer_ticker"] == "ABCH"
    assert at.session_state["explorer_form_types"] == ["8-K"]
    assert at.session_state["explorer_sort_label"] == "Importance score"
    assert at.session_state["explorer_ascending"] is True


# ── date mode switch ──────────────────────────────────────────────────────────


def test_switching_to_range_mode_reveals_range_input_and_persists():
    at = _run()
    at.radio(key=_w(at, "explorer_date_mode")).set_value("Rango")
    at.run()

    assert at.session_state["explorer_date_mode"] == "Rango"
    assert len(at.date_input) == 1  # now the range widget, not the single one

    at.text_input(key=_w(at, "explorer_ticker")).set_value("ABCH")
    at.run()
    assert at.session_state["explorer_date_mode"] == "Rango"


def test_range_mode_defaults_to_the_last_day_not_the_full_history():
    """No default span like 2024-01-01 -> 2026-08-25 — the range collapses
    to the same last-downloaded day until the user changes it."""
    at = _run()
    at.radio(key=_w(at, "explorer_date_mode")).set_value("Rango")
    at.run()

    filters = at.session_state["_test_filters"]
    assert filters.date_from == filters.date_to == "20260825"


# ── "Limpiar filtros" ─────────────────────────────────────────────────────────
#
# Resetting pagination to page 1 whenever any *other* filter changes is
# event_explorer.py's pre-existing filter-signature comparison (unchanged by
# this feature). Only the "page 1" part of the Limpiar filtros button itself
# belongs to this module, so that's what's tested here.


def test_clear_filters_bumps_the_reset_counter():
    """This is what forces every widget to remount so the reset is visible
    on screen, not just correct in session_state (see module docstring)."""
    at = _run()
    counter_before = at.session_state[RESET_COUNTER_KEY]
    at.button[0].click().run()
    assert at.session_state[RESET_COUNTER_KEY] == counter_before + 1


def test_clear_filters_restores_every_default_and_page_one():
    at = _run()
    at.text_input(key=_w(at, "explorer_ticker")).set_value("ABCH")
    at.multiselect(key=_w(at, "explorer_form_types")).select("8-K")
    at.radio(key=_w(at, "explorer_date_mode")).set_value("Rango")
    at.selectbox(key=_w(at, "explorer_sort_label")).select("Importance score")
    at.toggle(key=_w(at, "explorer_ascending")).set_value(True)
    at.run()
    at.session_state["explorer_page"] = 4
    at.run()

    at.button[0].click().run()

    assert at.session_state["explorer_date_mode"] == "Día"
    assert at.session_state["explorer_single_date"] == date(2026, 8, 25)
    assert at.session_state["explorer_ticker"] == ""
    assert at.session_state["explorer_form_types"] == []
    assert at.session_state["explorer_sort_label"] == "Fecha"
    assert at.session_state["explorer_ascending"] is False
    assert at.session_state["explorer_page"] == 1
