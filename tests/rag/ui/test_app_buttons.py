"""예시 질문 버튼·대화 지우기 버튼(11/09)을 Streamlit AppTest 로 확인한다. Neo4j·LLM 없이 실행한다.

설명
- service.build_resources / service.ask 를 가짜로 바꿔 화면의 버튼 흐름만 본다. 실제 답변 품질은 확인하지 않는다.
- QUERY_LOG=0 으로 질문 이력 파일(data/interim/ui_history)에 쓰지 않게 한다.
- streamlit 이 없으면 건너뛴다. 실행: uv run --with streamlit pytest tests/rag/ui/test_app_buttons.py -q
"""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from rag.ui import service

APP = str(Path(__file__).resolve().parents[3] / "src" / "rag" / "ui" / "app.py")


@pytest.fixture
def calls(monkeypatch):
    """가짜 ask 호출 기록(질문 목록)을 돌려준다. 같은 질문이 몇 번 실행됐는지 센다."""
    monkeypatch.setenv("QUERY_LOG", "0")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    asked: list[str] = []

    def fake_resources():
        return {"documents": {"doc_a": {"title": "문서 A"}}, "deps": {}}

    def fake_ask(res, question, region, allowed, polish=True, on_event=None, on_line=None):
        asked.append(question)
        output = {"status": "ANSWERED", "answer": f"답: {question}", "citations": [], "warnings": [],
                  "response_type": "DOMAIN", "tools_used": [], "findings": [], "search_rounds": 1}
        return {"output": output, "trace": [], "tool_results": [], "seconds": 0.1, "request_id": f"ui-{len(asked)}"}

    monkeypatch.setattr(service, "build_resources", fake_resources)
    monkeypatch.setattr(service, "ask", fake_ask)
    return asked


def _run() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    return at


def _button(at: AppTest, key: str):
    return next(b for b in at.button if b.key == key)


def test_empty_screen_shows_examples_and_no_clear_button(calls):
    at = _run()
    keys = [b.key for b in at.button]
    assert [k for k in keys if k.startswith("example-")] == ["example-0", "example-1", "example-2"]
    assert "clear-conversation" not in keys


def test_example_button_runs_the_question_exactly_once(calls):
    at = _run()
    _button(at, "example-0").click().run()
    assert not at.exception
    assert len(calls) == 1 and calls[0].startswith("의사가 하는 신고와 학교장 등이")
    assert len(at.session_state["history"]) == 1
    assert "pending_question" not in at.session_state       # 맡긴 질문은 한 번 쓰고 비워진다
    at.run()                                                # 아무것도 누르지 않은 재실행
    assert len(calls) == 1 and len(at.session_state["history"]) == 1   # 다시 실행되거나 쌓이지 않는다
    keys = [b.key for b in at.button]
    assert "clear-conversation" in keys and not any(k.startswith("example-") for k in keys)


def test_typed_question_wins_over_pending_example(calls):
    at = _run()
    at.session_state["pending_question"] = "예시 질문"
    at.chat_input[0].set_value("직접 입력한 질문").run()
    assert calls == ["직접 입력한 질문"]


def test_clear_keeps_saved_answers_and_shows_examples_again(calls):
    at = _run()
    _button(at, "example-1").click().run()
    # 저장한 답변(saved)은 화면 대화(history)와 별개다
    at.session_state["saved"] = [{"id": "x", "ts": "t", "question": "q", "status": "ANSWERED", "answer": "a",
                                  "sources": [], "notice": None, "notes": []}]
    _button(at, "clear-conversation").click().run()
    assert not at.exception
    assert at.session_state["history"] == []
    assert len(at.session_state["saved"]) == 1
    assert [b.key for b in at.button if b.key.startswith("example-")] == ["example-0", "example-1", "example-2"]
    # 지운 뒤에도 다음 질문이 정상 실행된다
    at.chat_input[0].set_value("다음 질문").run()
    assert calls[-1] == "다음 질문" and len(at.session_state["history"]) == 1
