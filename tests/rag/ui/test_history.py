"""UI 질문 이력 저장·집계 검사 (유종혁). Streamlit·DB 없이 실행한다."""
from datetime import datetime

from rag.ui import history

RESULT = {
    "output": {"status": "PARTIAL", "response_type": "DOMAIN", "tools_used": ["legal_search"],
               "findings": [{}, {}], "citations": [{}]},
    "trace": [{"step": "classify_intent", "intent": "LEGAL"}],
    "seconds": 4.0, "request_id": "ui-abc",
}


def test_make_record_keeps_summary_fields_only():
    rec = history.make_record("Q10 질문", RESULT, polish=True, now=datetime(2026, 11, 7, 10, 0, tzinfo=history.KST))
    assert rec["ts"] == "2026-11-07T10:00:00+09:00"
    assert (rec["status"], rec["intent"], rec["tools"], rec["findings"], rec["citations"]) == \
        ("PARTIAL", "LEGAL", ["legal_search"], 2, 1)
    assert "answer" not in rec                      # 답변 본문은 저장하지 않는다


def test_error_result_is_recorded():
    rec = history.make_record("질문", {"error": "ConnectionError: x"}, polish=False)
    assert rec["status"] == "ERROR" and rec["tools"] == []


def test_append_load_roundtrip_and_bad_line_skipped(tmp_path):
    path = tmp_path / "sub" / "q.jsonl"             # 폴더가 없어도 만들어야 한다
    assert history.append_record(history.make_record("a", RESULT, polish=True), path)
    with path.open("a", encoding="utf-8") as f:
        f.write("깨진 줄\n")
    assert history.append_record(history.make_record("b", RESULT, polish=True), path)
    assert [r["question"] for r in history.load_records(path)] == ["a", "b"]


def test_missing_file_gives_empty_list(tmp_path):
    assert history.load_records(tmp_path / "none.jsonl") == []


def test_summarize_groups_same_question_by_normalized_text():
    records = [
        {"ts": "2026-11-07T10:00:00+09:00", "question": "홍역  신고는?", "status": "ANSWERED", "intent": "LEGAL",
         "tools": ["legal_search"], "seconds": 2.0},
        {"ts": "2026-11-07T11:00:00+09:00", "question": " 홍역 신고는? ", "status": "PARTIAL", "intent": "LEGAL",
         "tools": ["legal_search", "action_search"], "seconds": 4.0},
        {"ts": "2026-11-08T09:00:00+09:00", "question": "다른 질문", "status": "ERROR", "intent": None,
         "tools": [], "seconds": None},
    ]
    s = history.summarize(records)
    assert s["total"] == 3 and s["top_questions"][0] == ("홍역  신고는?", 2)
    assert s["by_status"] == {"ANSWERED": 1, "PARTIAL": 1, "ERROR": 1}
    assert s["by_tool"] == {"legal_search": 2, "action_search": 1}
    assert s["by_day"] == {"2026-11-07": 2, "2026-11-08": 1}
    assert s["avg_seconds"] == 3.0


def test_logging_can_be_disabled(monkeypatch):
    monkeypatch.setenv("QUERY_LOG", "0")
    assert not history.logging_enabled()


def test_default_path_can_be_redirected(tmp_path, monkeypatch):
    # 기본 경로는 호출 때 정해져서, LOG_PATH 를 바꾸면 실제 이력 파일을 건드리지 않는다
    monkeypatch.setattr(history, "LOG_PATH", tmp_path / "redirected.jsonl")
    assert history.append_record(history.make_record("z", RESULT, polish=True))
    assert [r["question"] for r in history.load_records()] == ["z"]


def test_non_object_lines_and_null_tools_do_not_break(tmp_path):
    path = tmp_path / "q.jsonl"
    path.write_text('123\n["a"]\n{"question": "q", "tools": null, "ts": "2026-11-07T10:00:00+09:00"}\n',
                    encoding="utf-8")
    records = history.load_records(path)
    assert len(records) == 1                         # 객체가 아닌 줄은 건너뛴다
    assert history.summarize(records)["by_tool"] == {}


def test_csv_safe_blocks_formula_cells():
    import pandas as pd

    frame = pd.DataFrame({"question": ["=1+1", "+cmd", "-x", "@y", "정상 질문"], "n": [1, 2, 3, 4, 5]})
    assert list(history.csv_safe(frame)["question"]) == ["'=1+1", "'+cmd", "'-x", "'@y", "정상 질문"]
