import sys

import streamlit as st
from dotenv import load_dotenv

from config import APP_TITLE, APP_ICON, PROJECT_ROOT, STYLES_PATH

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from screens import executive_summary, event_explorer, filing_detail, process_filings, statistics

st.set_page_config(
    page_title=APP_TITLE,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)


def _inject_css() -> None:
    css = STYLES_PATH.read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def _render_topbar() -> None:
    st.markdown(
        f'<div class="app-topbar"><span class="brand-mark">{APP_ICON}</span>{APP_TITLE}</div>',
        unsafe_allow_html=True,
    )


def main() -> None:
    _inject_css()
    _render_topbar()

    pages = {
        "resumen": st.Page(executive_summary.render, title="Resumen Ejecutivo", default=True),
        "explorador": st.Page(event_explorer.render, title="Explorador de Eventos", url_path="explorador"),
        "detalle": st.Page(filing_detail.render, title="Detalle del Filing", url_path="detalle"),
        "estadisticas": st.Page(statistics.render, title="Estadísticas", url_path="estadisticas"),
        "procesar": st.Page(process_filings.render, title="Procesar filings", url_path="procesar"),
    }
    st.session_state["nav_pages"] = pages

    navigation = st.navigation(list(pages.values()))
    navigation.run()


if __name__ == "__main__":
    main()
