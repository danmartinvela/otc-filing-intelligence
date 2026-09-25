import streamlit as st


def render_meter(label: str, percentage: float, caption: str) -> None:
    pct = max(0.0, min(100.0, percentage))
    html = (
        '<div class="meter">'
        f'<div class="detail-label">{label}</div>'
        f'<div class="meter-value">{pct:.0f}%</div>'
        '<div class="meter-track">'
        f'<div class="meter-fill" style="width:{pct}%"></div>'
        "</div>"
        f'<div class="kpi-caption">{caption}</div>'
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)
