"""Bordered 'section card' shell used to frame a chart or a block of content.

Splitting an opening/closing <div> across two separate st.markdown calls does
NOT nest whatever is rendered between them — each st.markdown call produces
its own independent DOM fragment, so the tag pair never actually wraps
anything (browsers silently auto-close the dangling tags). st.container(key=…)
is the real fix: Streamlit gives that container's wrapper element a stable
`st-key-<key>` class (see styles/main.css) that we style as a card, and
everything rendered inside the `with` block is genuinely nested inside it.
"""
from contextlib import contextmanager

import streamlit as st


@contextmanager
def section_card(title: str, key: str):
    with st.container(key=f"section-{key}"):
        st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)
        yield
