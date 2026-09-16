"""A minimal, deterministic fake of the `streamlit` module -- just enough
surface for `app.py`'s sidebar/account-selector logic to run under pytest
without the real `streamlit` package installed (it isn't, in this
environment) and without a real browser/session.

This is intentionally narrow: it does not attempt to fake the main-body
rendering calls (`st.tabs`, `st.metric`, `st.expander`, ...) because the
account-selector tests only need to exercise `_render_sidebar` /
`_render_account_selector`, never `main()`'s full dashboard body. If a
future test needs to drive `main()` end-to-end, extend `FakeSt` rather than
building a second fake.

Usage:

    import sys
    from tests.fake_streamlit import FakeSt
    fake_st = FakeSt()
    sys.modules["streamlit"] = fake_st          # BEFORE `import app`
    import app                                    # app.py's `import streamlit as st` now binds to fake_st
"""

from __future__ import annotations

from typing import Any


class FakeSessionState(dict):
    """Streamlit's `st.session_state` supports both `st.session_state.x`
    and `st.session_state["x"]` / `.get("x")` -- this mirrors that with a
    plain dict plus attribute access, which is all app.py relies on.
    """

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value


class FakeSidebar:
    """Records every call so a test can assert what was rendered, and lets
    a test script `selectbox`'s return value to simulate a user picking a
    particular dropdown option -- the one interaction this feature
    actually depends on.
    """

    def __init__(self) -> None:
        self.selectbox_calls: list[dict[str, Any]] = []
        self.subheader_calls: list[str] = []
        self.button_return_values: dict[str, bool] = {}
        self.text_input_return_values: dict[str, str] = {}
        self.file_uploader_return_value: Any = None
        self.errors: list[str] = []
        # When set, the NEXT selectbox() call returns this value instead of
        # falling back to `options[index]` -- simulates the user choosing a
        # specific option rather than whatever the default index is.
        self.next_selectbox_return: Any = None

    def header(self, *a, **k) -> None:
        pass

    def subheader(self, text: str, *a, **k) -> None:
        self.subheader_calls.append(text)

    def divider(self, *a, **k) -> None:
        pass

    def caption(self, *a, **k) -> None:
        pass

    def error(self, text: str, *a, **k) -> None:
        self.errors.append(text)

    def text_input(self, label: str, *a, **k) -> str:
        return self.text_input_return_values.get(label, "")

    def button(self, label: str, *a, **k) -> bool:
        return self.button_return_values.get(label, False)

    def file_uploader(self, *a, **k) -> Any:
        return self.file_uploader_return_value

    def selectbox(self, label: str, options: list, *, index: int = 0, key: str | None = None):
        self.selectbox_calls.append({"label": label, "options": list(options), "index": index, "key": key})
        if self.next_selectbox_return is not None:
            value = self.next_selectbox_return
            self.next_selectbox_return = None
            return value
        return options[index]


class FakeSpinner:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSt:
    """The `streamlit` module stand-in. Only the surface `_render_sidebar`
    / `_render_account_selector` actually touch is implemented; anything
    else raises AttributeError so a test relying on unimplemented surface
    fails loudly rather than silently no-opping.
    """

    def __init__(self) -> None:
        self.session_state = FakeSessionState()
        self.sidebar = FakeSidebar()

    def spinner(self, *a, **k):
        return FakeSpinner()

    def set_page_config(self, *a, **k) -> None:
        pass

    def title(self, *a, **k) -> None:
        pass

    def caption(self, *a, **k) -> None:
        pass

    def info(self, *a, **k) -> None:
        pass

    def header(self, *a, **k) -> None:
        pass
