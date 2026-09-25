from contextlib import contextmanager

import streamlit as st


@contextmanager
def section_card(title: str, key: str):
    with st.container(key=f"section-{key}"):
        st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)
        yield
