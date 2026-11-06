"""근거 문서 목록의 안전 처리 검사 (유종혁). Streamlit·DB 없이 실행한다.

설명: 문서명·판본·URL 이 마크다운 문법을 깨지 못하게 하는지, 내부 처리 문구가 사용자 화면으로 새지 않는지 확인한다.
"""
from rag.ui import sources


def test_markdown_special_chars_in_title_and_version_are_escaped():
    group = {"document_id": "d", "title": "문서 [x](http://evil) *굵게*", "version": "v_1", "pages": [3], "printed": []}
    line = sources.source_line(group, None)
    assert "[x](http://evil)" not in line          # 링크 문법이 그대로 남으면 안 된다
    assert "\\[x\\]\\(http://evil\\)" in line and "v\\_1" in line


def test_url_with_parentheses_and_spaces_is_encoded_and_non_http_dropped(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text("document_id,공식URL\nA,https://kdca.go.kr/a b)(c\nB,http://\nC,ftp://x.kr/f\n", encoding="utf-8-sig")
    urls = sources.load_urls(path)
    assert set(urls) == {"A"}
    assert " " not in urls["A"] and ")" not in urls["A"] and "(" not in urls["A"]


def test_user_warnings_drop_internal_and_limit():
    warns = ["legal_search 결과 중 DB 대조 실패 Finding 1개를 제외했다.", "이 답은   의사의 보고만 다룹니다.", "도구 연결 안 됨"]
    assert sources.user_warnings(warns) == ["이 답은 의사의 보고만 다룹니다."]
    assert len(sources.user_warnings([f"문구 {i}" for i in range(9)])) == 5
