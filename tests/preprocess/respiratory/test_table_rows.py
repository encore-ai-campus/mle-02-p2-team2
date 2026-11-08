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
