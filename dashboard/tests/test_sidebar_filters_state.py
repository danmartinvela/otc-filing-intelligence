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
    return _widget_key(state_key, at.session_state[RESET_COUNTER_KEY])




def test_resolve_last_downloaded_date_uses_max_event_date():
    assert resolve_last_downloaded_date({"min_date": "20240101", "max_date": "20260825"}) == date(2026, 8, 25)


def test_resolve_last_downloaded_date_falls_back_to_today_when_no_filings():
    assert resolve_last_downloaded_date({"min_date": None, "max_date": None}) == date.today()


def test_resolve_last_downloaded_date_falls_back_on_missing_keys():
    assert resolve_last_downloaded_date({}) == date.today()




def test_defaults_on_first_render():
    at = _run()
    assert at.session_state["explorer_date_mode"] == "Día"
    assert at.session_state["explorer_single_date"] == date(2026, 8, 25)
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




def test_ticker_change_persists_across_unrelated_rerun():
    at = _run()
    at.text_input(key=_w(at, "explorer_ticker")).set_value("ABCH")
    at.run()
    assert at.session_state["explorer_ticker"] == "ABCH"

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

    at.run()

    assert at.session_state["explorer_ticker"] == "ABCH"
    assert at.session_state["explorer_form_types"] == ["8-K"]
    assert at.session_state["explorer_sort_label"] == "Importance score"
    assert at.session_state["explorer_ascending"] is True




def test_switching_to_range_mode_reveals_range_input_and_persists():
    at = _run()
    at.radio(key=_w(at, "explorer_date_mode")).set_value("Rango")
    at.run()

    assert at.session_state["explorer_date_mode"] == "Rango"
    assert len(at.date_input) == 1

    at.text_input(key=_w(at, "explorer_ticker")).set_value("ABCH")
    at.run()
    assert at.session_state["explorer_date_mode"] == "Rango"


def test_range_mode_defaults_to_the_last_day_not_the_full_history():
    at = _run()
    at.radio(key=_w(at, "explorer_date_mode")).set_value("Rango")
    at.run()

    filters = at.session_state["_test_filters"]
    assert filters.date_from == filters.date_to == "20260825"




def test_clear_filters_bumps_the_reset_counter():
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
