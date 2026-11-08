import pytest

from preprocess.respiratory.table_rows import document_table_rows, page_table_rows, summarize


def test_each_row_keeps_heading_header_and_stable_page_id():
    page = """### 감염병별 기준

| 질병 | 기간 |
| --- | --- |
| 가 | 1일 |
| 나 | 2일 |
"""
    rows, warnings = page_table_rows("test_document", 39, page)
    assert [row["chunk_id"] for row in rows] == [
        "test_document:p0039:t01r01", "test_document:p0039:t01r02",
    ]
    assert rows[1]["text"] == "감염병별 기준\n| 질병 | 기간 |\n| 나 | 2일 |"
    assert rows[1]["pdf_page"] == 39
    assert not warnings


def test_ragged_empty_and_html_tables_are_not_silently_accepted():
    page = """| 구분 | 내용 |
| --- | --- |
| | 이어지는 내용 |
| 가 | 나 | 다 |

<table><tr><td>합쳐진 셀</td></tr></table>
"""
    rows, warnings = page_table_rows("test_document", 1, page)
    assert "빈 셀·병합 여부 확인 필요" in rows[0]["review_flags"]
    assert "열 개수 불일치" in rows[1]["review_flags"]
    assert all("표 제목 확인 필요" in row["review_flags"] for row in rows)
    assert warnings == [{"pdf_page": 1, "table": 2, "reason": "HTML 표 수동 검토"}]
    assert summarize(rows, warnings)["rows_needing_review"] == 2


def test_pages_and_escaped_pipe_do_not_change_column_count():
    text = """## PDF 3페이지
### 제목
| 항목 | 설명 |
| --- | --- |
| 가 | 왼쪽\\|오른쪽 |
## PDF 4페이지
### 제목
| 항목 | 설명 |
| --- | --- |
| 나 | 설명 |
"""
    rows, warnings = document_table_rows("test_document", text)
    assert [r["chunk_id"] for r in rows] == [
        "test_document:p0003:t01r01", "test_document:p0004:t01r01",
    ]
    assert rows[0]["review_flags"] == ["PDF 표 제목·머리글·행 대조 필요"]
    assert not warnings


def test_unsupported_single_column_table_is_reported_and_preserves_table_order():
    page = """### 구분
| 내용 |
| --- |
| 한 열 |

| 구분 | 설명 |
| --- | --- |
| 가 | 둘째 표 |
"""
    rows, warnings = page_table_rows("test_document", 3, page)
    assert warnings == [{"pdf_page": 3, "table": 1, "reason": "1열 표 수동 검토"}]
    assert rows[0]["chunk_id"] == "test_document:p0003:t02r01"


def test_body_tables_on_same_page_keep_explicit_distinct_one_based_table_ids():
    text = """## PDF 113페이지 · 표 1
| 항목 | 내용 |
| --- | --- |
| 진단 | 첫 표 원문 |

## PDF 113페이지 · 표 2
| 구분 | 기준 |
| --- | --- |
| 환자 | 둘째 표 원문 |
"""
    rows, warnings = document_table_rows("kdca_respiratory_2026", text)
    assert [row["chunk_id"] for row in rows] == [
        "kdca_respiratory_2026:p0113:t01r01",
        "kdca_respiratory_2026:p0113:t02r01",
    ]
    assert rows[0]["row_text"] == "| 진단 | 첫 표 원문 |"
    assert rows[1]["header"] == "| 구분 | 기준 |"
    assert rows[1]["table_title"] == "PDF 113페이지 · 표 2"
    assert all(row["pdf_page"] == 113 for row in rows)
    assert not warnings


@pytest.mark.parametrize("comment", ["(25페이지 표 2 이어짐)", "（이어서）"])
def test_body_table_heading_accepts_parenthetical_comment_without_changing_row(comment):
    text = f"""## PDF 26페이지 · 표 1 {comment}
| 항목 | 내용 |
| --- | --- |
| 기준 | 원문<br>이어지는 설명 |
"""
    rows, warnings = document_table_rows("kdca_respiratory_2026", text)
    assert rows[0]["chunk_id"] == "kdca_respiratory_2026:p0026:t01r01"
    assert comment in rows[0]["table_title"]
    assert rows[0]["row_text"] == "| 기준 | 원문<br>이어지는 설명 |"
    assert rows[0]["printed_page"] is None
    assert not warnings


def test_flu_body_table_heading_with_dash_keeps_title():
    text = """## PDF 9페이지 — 인플루엔자 약제 기준
| 약제 | 기준 |
| --- | --- |
| 약제 A | 원문 기준 |
"""
    rows, warnings = document_table_rows("kdca_flu_facility_2026", text)
    assert rows[0]["chunk_id"] == "kdca_flu_facility_2026:p0009:t01r01"
    assert rows[0]["table_title"] == "PDF 9페이지 — 인플루엔자 약제 기준"
    assert rows[0]["header"] == "| 약제 | 기준 |"
    assert not warnings


def test_duplicate_explicit_body_table_number_is_rejected():
    table = "## PDF 113페이지 · 표 1\n| 가 | 나 |\n| --- | --- |\n| 값 | 원문 |\n"
    with pytest.raises(ValueError, match="중복.*본문표"):
        document_table_rows("kdca_respiratory_2026", table + table)


def test_two_unnumbered_body_tables_on_same_page_get_one_based_ids():
    table = "| 항목 | 내용 |\n| --- | --- |\n| 기준 | 원문 |\n"
    text = f"## PDF 9페이지 — 첫 표\n{table}\n## PDF 9페이지 — 둘째 표\n{table}"
    rows, warnings = document_table_rows("kdca_flu_facility_2026", text)
    assert [row["chunk_id"] for row in rows] == [
        "kdca_flu_facility_2026:p0009:t01r01",
        "kdca_flu_facility_2026:p0009:t02r01",
    ]
    assert not warnings
