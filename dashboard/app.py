"""Entry point: page config, global CSS, and navigation shell.

Run with: streamlit run dashboard/app.py
Read-only, with one deliberate exception: screens/process_filings.py is the
only page that writes to data/filings.db, by calling the existing backend
pipeline (src/pipeline.py) — same as the CLI. Every other page only ever
SELECTs (see database/connection.py).
"""
import sys

import streamlit as st
from dotenv import load_dotenv

from config import APP_TITLE, APP_ICON, PROJECT_ROOT, STYLES_PATH

# The backend package (src/) lives one directory above dashboard/, which
# isn't on sys.path by default (Streamlit only adds the running script's own
# directory). Needed before importing screens.process_filings, which imports
# from src.pipeline.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Same .env the CLI reads (SEC_USER_AGENT, LLM_API_KEY/LLM_BASE_URL/LLM_MODEL)
# — needed for screens/process_filings.py.
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
        # No explicit url_path here: a default=True page with its own named
        # url_path 404s on a hard reload / fresh tab at that URL (Streamlit
        # only reliably resolves the default page at "/"). Leaving it
        # unnamed means it's only ever reached at "/", which always works.
        "resumen": st.Page(executive_summary.render, title="Resumen Ejecutivo", default=True),
        "explorador": st.Page(event_explorer.render, title="Explorador de Eventos", url_path="explorador"),
        "detalle": st.Page(filing_detail.render, title="Detalle del Filing", url_path="detalle"),
        "estadisticas": st.Page(statistics.render, title="Estadísticas", url_path="estadisticas"),
        "procesar": st.Page(process_filings.render, title="Procesar filings", url_path="procesar"),
    }
    # Stashed so any page can st.switch_page(st.session_state.nav_pages["detalle"]) —
    # st.switch_page needs the actual StreamlitPage object, not just its title/url.
    st.session_state["nav_pages"] = pages

    navigation = st.navigation(list(pages.values()))
    navigation.run()


if __name__ == "__main__":
    main()
