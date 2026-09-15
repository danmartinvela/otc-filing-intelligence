import sys
from pathlib import Path

# Bare imports like `from components.sidebar_filters import ...` only work
# with dashboard/ itself on sys.path — true when Streamlit runs app.py
# (it adds the running script's own directory), but not guaranteed for
# pytest depending on invocation directory. Make it explicit here instead.
_DASHBOARD_ROOT = Path(__file__).resolve().parents[1]
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))
