"""Render every Streamlit page headlessly (AppTest) on a reconciled demo database."""
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app.py"
URL_PATHS = re.findall(r'url_path\s*=\s*"([^"]+)"', APP.read_text(encoding="utf-8")) or [None]


@pytest.mark.parametrize("url_path", URL_PATHS)
def test_page_renders_without_exceptions(reconciled_demo, url_path):
    from streamlit.testing.v1 import AppTest
    from streamlit.util import calc_hash
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    if url_path:
        at._page_hash = calc_hash(url_path)      # st.Page(callable) pages are keyed by url_path
        at.run()
    assert not at.exception, [e.value for e in at.exception]
