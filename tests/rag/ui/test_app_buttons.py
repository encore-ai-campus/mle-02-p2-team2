"""예시 질문 버튼·대화 지우기 버튼·답변/근거 두 칸 배치(11/09)를 Streamlit AppTest 로 확인한다. Neo4j·LLM 없이 실행한다.

설명
- service.build_resources / service.ask 를 가짜로 바꿔 화면의 버튼 흐름만 본다. 실제 답변 품질은 확인하지 않는다.
- QUERY_LOG=0 으로 질문 이력 파일(data/interim/ui_history)에 쓰지 않게 한다.
- streamlit 이 없으면 건너뛴다. 실행: uv run --with streamlit pytest tests/rag/ui/test_app_buttons.py -q
"""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
import streamlit as st
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
    st.cache_resource.clear()      # app.py 의 resources() 캐시가 시험 사이에 남아 이전 가짜 자원을 쓰지 않게 한다
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


def test_domain_answer_is_split_into_answer_and_sources_columns(calls):
    # 시안 C: 감염병(DOMAIN) 답변은 왼쪽 답변, 오른쪽 근거 문서 두 칸이다
    at = _run()
    _button(at, "example-0").click().run()
    assert not at.exception
    assert len(at.columns) == 2
    left = " ".join(m.value for m in at.columns[0].markdown)
    right = " ".join(m.value for m in at.columns[1].markdown)
    assert "답: 의사가 하는 신고와" in left and "panel-title" in left
    assert "근거 문서" in right


def test_non_domain_answer_has_no_sources_column(calls, monkeypatch):
    def general_ask(res, question, region, allowed, polish=True, on_event=None, on_line=None):
        output = {"status": "ANSWERED", "answer": "일반 안내", "citations": [], "warnings": [],
                  "response_type": "GENERAL", "tools_used": [], "findings": [], "search_rounds": 0}
        return {"output": output, "trace": [], "tool_results": [], "seconds": 0.1, "request_id": "ui-g"}

    monkeypatch.setattr(service, "ask", general_ask)
    at = _run()
    at.chat_input[0].set_value("안녕").run()
    assert not at.exception
    assert len(at.columns) == 0           # 근거 칸을 만들지 않는다
    assert any("일반 안내" in m.value for m in at.markdown)


def test_developer_view_keeps_two_columns_and_extra_tabs(calls, monkeypatch):
    # 개발자 화면(?dev=1, 로컬 시험용 DEV_ALLOW_OPEN=1)도 같은 두 칸 배치 아래에 점검 탭을 그대로 둔다
    monkeypatch.delenv("DEV_PASSWORD", raising=False)
    monkeypatch.setenv("DEV_ALLOW_OPEN", "1")
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["dev"] = "1"
    at.run()
    assert not at.exception
    _button(at, "example-0").click().run()
    assert not at.exception
    # 개발자 화면은 처리 흐름의 지표 칸 등 다른 칸도 있으므로 개수 대신 앞의 두 칸(답변·근거 문서)을 확인한다
    assert any("답: 의사가 하는 신고와" in m.value for m in at.columns[0].markdown)
    assert any("근거 문서" in m.value for m in at.columns[1].markdown)
    labels = [t.label for t in at.tabs]
    assert any(label.startswith("출처") for label in labels) and "처리 흐름" in labels and "그래프 경로" in labels
    assert any(label.startswith("주의·한계") for label in labels)
    assert any("사용한 도구" in c.value for c in at.caption)


def _install_partial_ask(monkeypatch, status: str, warnings: list[str], citations: list[dict]):
    """상태·경고·인용을 지정한 가짜 ask 와 문서 2개짜리 가짜 resources 를 끼운다."""
    monkeypatch.setattr(service, "build_resources", lambda: {
        "documents": {"doc_a": {"title": "문서 A", "version": "2026"}, "doc_b": {"title": "문서 B"}}, "deps": {}})

    def fake_ask(res, question, region, allowed, polish=True, on_event=None, on_line=None):
        output = {"status": status, "answer": "일부 답", "citations": citations, "warnings": warnings,
                  "response_type": "DOMAIN", "tools_used": [], "findings": [], "search_rounds": 1}
        return {"output": output, "trace": [], "tool_results": [], "seconds": 0.1, "request_id": "ui-p"}

    monkeypatch.setattr(service, "ask", fake_ask)


CITATIONS = [{"document_id": "doc_a", "pdf_page": 3, "printed_page": None, "chunk_id": "doc_a:p0003:b01", "excerpt": "x"},
             {"document_id": "doc_b", "pdf_page": 9, "printed_page": 5, "chunk_id": "doc_b:p0009:b01", "excerpt": "y"}]


def test_partial_answer_keeps_notice_and_unresolved_note_in_sources_column(calls, monkeypatch):
    # 안내와 '확인이 필요한 부분'은 오른쪽 근거 칸의 카드 뒤에 있다(사라지지 않는다). 카드는 문서마다 한 장, 번호 없음.
    _install_partial_ask(monkeypatch, "PARTIAL", ["[미해결] 근거에서 확인하지 못한 부분: 접촉자 범위"], CITATIONS)
    at = _run()
    _button(at, "example-0").click().run()
    assert not at.exception
    right = at.columns[1]
    texts = [m.value for m in right.markdown] + [c.value for c in right.caption]
    assert "**문서 A**" in texts and "**문서 B**" in texts                               # 문서명 카드
    chips = " ".join(t for t in texts if "src-chip" in t)
    assert ">PDF 3쪽<" in chips and ">PDF 9쪽<" in chips and ">인쇄 5쪽<" in chips and ">판본 2026<" in chips   # 칩
    assert not any(t.startswith("[1]") for t in texts)                      # 카드에 번호를 붙이지 않는다
    assert any("일부만 확인한 답변입니다" in t for t in texts)
    assert any(e.label.startswith("확인이 필요한 부분") for e in right.expander)
    left = " ".join(m.value for m in at.columns[0].markdown)
    assert "일부 답" in left and "일부만 확인한 답변입니다" not in left        # 안내는 근거 칸에만 있다


def test_insufficient_evidence_shows_notice_without_cards(calls, monkeypatch):
    _install_partial_ask(monkeypatch, "INSUFFICIENT_EVIDENCE", [], [])
    at = _run()
    _button(at, "example-0").click().run()
    assert not at.exception
    right = at.columns[1]
    assert any("표시할 출처가 없습니다" in c.value for c in right.caption)
    assert any("제공된 공식 자료에서 확인하지 못했습니다" in i.value for i in right.info)


def test_general_user_view_hides_developer_extras(calls):
    # 일반 사용자 화면에는 사용한 도구 캡션도 개발자 탭도 없다
    at = _run()
    _button(at, "example-0").click().run()
    assert not at.exception
    assert not any("사용한 도구" in c.value for c in at.caption)
    assert len(at.tabs) == 2 and at.tabs[0].label == "질문하기"


def test_question_is_styled_line_and_rendered_as_plain_markdown(calls):
    # 질문은 사용자 입력이라 HTML 로 그리지 않고 일반 마크다운으로 그린다(스타일은 container key 클래스)
    at = _run()
    at.chat_input[0].set_value("<b>질문</b> 입니다").run()
    assert not at.exception
    values = [m.value for m in at.markdown]
    assert "<b>질문</b> 입니다" in values                                              # 그대로 마크다운 본문으로 전달
    assert not any(v.startswith('<div class="qline') for v in values)


# ---------------------------------------------------------------- 사이드바 질문 기록(11/09)

def _hist_buttons(at: AppTest) -> list:
    return [b for b in at.sidebar.button if b.key.startswith("hist-pick-")]


def _ask_two(at: AppTest) -> None:
    at.chat_input[0].set_value("첫 질문").run()
    at.chat_input[0].set_value("둘째 질문").run()


def _main_text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown)


def test_sidebar_history_empty_then_lists_questions_newest_first(calls):
    at = _run()
    assert _hist_buttons(at) == []
    assert any("아직 질문이 없습니다" in c.value for c in at.sidebar.caption)
    _ask_two(at)
    assert not at.exception
    assert [b.label for b in _hist_buttons(at)] == ["✅ 둘째 질문", "✅ 첫 질문"]       # 새것부터, 상태 아이콘 포함


def test_sidebar_pick_shows_only_that_question_without_asking_again(calls):
    at = _run()
    _ask_two(at)
    n_calls = len(calls)
    _hist_buttons(at)[1].click().run()                  # 오래된 '첫 질문' 선택
    assert not at.exception
    assert len(calls) == n_calls                        # 다시 질문(LLM 호출)하지 않는다
    text = _main_text(at)
    assert "답: 첫 질문" in text and "답: 둘째 질문" not in text
    assert any(b.key == "hist-all-main" for b in at.button)
    # 전체 보기로 돌아가면 둘 다 보인다
    _button(at, "hist-all-main").click().run()
    text = _main_text(at)
    assert "답: 첫 질문" in text and "답: 둘째 질문" in text


def test_new_question_clears_pick_and_clear_conversation_empties_sidebar(calls):
    at = _run()
    _ask_two(at)
    _hist_buttons(at)[1].click().run()
    at.chat_input[0].set_value("셋째 질문").run()       # 새 질문이면 고른 표시를 풀고 새 답을 보여 준다
    assert "focus_hid" not in at.session_state
    assert "답: 셋째 질문" in _main_text(at)
    _button(at, "clear-conversation").click().run()
    assert _hist_buttons(at) == []
    assert any("아직 질문이 없습니다" in c.value for c in at.sidebar.caption)


def test_sidebar_history_label_truncates_and_keeps_full_question_in_help(calls):
    at = _run()
    long_q = "아주 긴 질문 " * 8
    at.chat_input[0].set_value(long_q).run()
    button = _hist_buttons(at)[0]
    assert button.label.endswith("…") and len(button.label) <= 2 + 26 + 1
    assert button.help == long_q


def test_developer_sidebar_keeps_settings_and_history(calls, monkeypatch):
    monkeypatch.delenv("DEV_PASSWORD", raising=False)
    monkeypatch.setenv("DEV_ALLOW_OPEN", "1")
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["dev"] = "1"
    at.run()
    at.chat_input[0].set_value("개발자 질문").run()
    assert not at.exception
    assert [b.label for b in _hist_buttons(at)] == ["✅ 개발자 질문"]
    assert any(h.value == "개발자 설정" for h in at.sidebar.header)
