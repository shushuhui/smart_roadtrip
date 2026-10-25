"""Streamlit 界面冒烟测试：确保脚本能正常渲染初始状态。"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parents[1] / "smart_roadtrip" / "ui" / "streamlit_app.py"


def test_streamlit_app_renders_initial_state() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.run()

    assert not app.exception
    assert any("自然语言" in info.value for info in app.info)

    labels = [button.label for button in app.sidebar.button]
    assert "解析并生成行程" in labels
    assert "生成行程" in labels
