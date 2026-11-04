"""온톨로지 v3.1 6절 전역 ID 규약의 school 임시 생성·검사 함수.

src/common/ids.py가 병합되면 그 모듈로 교체한다. 번호(블록·표·행·행동)를 '어떻게 정할지'는
B04(출처별 ID와 매핑 고정)에서 정한다. 이 모듈은 형식만 만들고 검사한다.
"""

from __future__ import annotations

import re

DOCUMENT_ID = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_DOC = r"(?P<document_id>[a-z][a-z0-9]*(?:_[a-z0-9]+)*)"
CHUNK_BLOCK_ID = re.compile(rf"^{_DOC}:p(?P<page>\d{{4}}):b(?P<block>\d{{2}})$")
CHUNK_ROW_ID = re.compile(rf"^{_DOC}:p(?P<page>\d{{4}}):t(?P<table>\d{{2}})r(?P<row>\d{{2}})$")
ACTION_ID = re.compile(rf"^{_DOC}:p(?P<page>\d{{4}}):a(?P<action>\d{{2}})$")
STANDARD_ID = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
CONTEXT_LABELS = {"Document", "Chunk", "Disease", "SchoolRole", "SchoolAction", "ActionConcept"}


class IdError(ValueError):
    """ID 형식 오류."""


def _check_number(name: str, value: int, upper: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
        raise IdError(f"{name}는 1~{upper} 정수: {value!r}")


def _check_document(document_id: str) -> None:
    if not isinstance(document_id, str) or not DOCUMENT_ID.match(document_id):
        raise IdError(f"document_id 형식 오류(소문자 snake_case): {document_id!r}")


def chunk_block_id(document_id: str, pdf_page: int, block: int) -> str:
    """문단 청크 ID: {document_id}:p{쪽4}:b{블록2}. pdf_page는 PDF 뷰어 기준(1부터)."""
    _check_document(document_id)
    _check_number("pdf_page", pdf_page, 9999)
    _check_number("block", block, 99)
    return f"{document_id}:p{pdf_page:04d}:b{block:02d}"


def chunk_row_id(document_id: str, pdf_page: int, table: int, row: int) -> str:
    """표 행 청크 ID: {document_id}:p{쪽4}:t{표2}r{행2}."""
    _check_document(document_id)
    _check_number("pdf_page", pdf_page, 9999)
    _check_number("table", table, 99)
    _check_number("row", row, 99)
    return f"{document_id}:p{pdf_page:04d}:t{table:02d}r{row:02d}"


def action_id(document_id: str, pdf_page: int, number: int) -> str:
    """SchoolAction ID: {document_id}:p{주 근거 청크 쪽4}:a{행동2}."""
    _check_document(document_id)
    _check_number("pdf_page", pdf_page, 9999)
    _check_number("number", number, 99)
    return f"{document_id}:p{pdf_page:04d}:a{number:02d}"


def parse_id(value: str) -> dict:
    """전역 ID를 종류·문서·쪽 등으로 나눈다. 형식이 틀리면 IdError."""
    for kind, pattern in (("chunk_block", CHUNK_BLOCK_ID), ("chunk_row", CHUNK_ROW_ID),
                          ("action", ACTION_ID)):
        match = pattern.match(value) if isinstance(value, str) else None
        if match:
            parts = {k: (int(v) if k != "document_id" else v) for k, v in match.groupdict().items()}
            if parts["page"] < 1:
                raise IdError(f"PDF 쪽은 1부터: {value!r}")
            return {"kind": kind, **parts}
    if isinstance(value, str) and DOCUMENT_ID.match(value):
        return {"kind": "document", "document_id": value}
    if isinstance(value, str) and STANDARD_ID.match(value):
        return {"kind": "standard", "id": value}
    raise IdError(f"전역 ID 형식이 아님: {value!r}")


def validate_context_entity_id(value: str) -> tuple[str, str]:
    """QueryInput.context_entity_ids 항목 '노드라벨:ID'를 검사해 (라벨, ID)로 돌려준다."""
    label, sep, ident = value.partition(":") if isinstance(value, str) else ("", "", "")
    if not sep or label not in CONTEXT_LABELS:
        raise IdError(f"'노드라벨:ID' 형식이 아님: {value!r}")
    parsed = parse_id(ident)
    expected = {"Document": {"document"}, "Chunk": {"chunk_block", "chunk_row"},
                "SchoolAction": {"action"}, "Disease": {"standard"},
                "SchoolRole": {"standard"}, "ActionConcept": {"standard"}}[label]
    if parsed["kind"] not in expected:
        raise IdError(f"{label}에 맞지 않는 ID: {ident!r}")
    return label, ident
