"""Consistent page header: title + subtitle on the left, last-updated on the right.

Pure presentation — callers supply already-formatted strings, this module has
no knowledge of the database.
"""
import streamlit as st


def render_page_header(title: str, subtitle: str = "", last_updated: str = "") -> None:
    # Single-line HTML: a blank/indented line inside unsafe_allow_html gets
    # parsed as a Markdown code block instead of raw HTML.
    last_updated_html = (
        f'<div class="last-updated">Última actualización<br>{last_updated}</div>'
        if last_updated
        else ""
    )
    html = (
        '<div class="page-header">'
        f'<div><h1>{title}</h1><div class="page-subtitle">{subtitle}</div></div>'
        f"{last_updated_html}"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)
