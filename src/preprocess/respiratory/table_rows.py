"""호흡기 Markdown 표를 검수용 행 후보로 나눈다. DB와 기존 청크는 바꾸지 않는다."""

from __future__ import annotations

import re
from collections import Counter

from index.school.ids_local import chunk_row_id
from preprocess.respiratory.md_to_blocks import (
    PAGE_HEADING,
    clean_page,
    clean_section_title,
    extract_printed_page,
)
from preprocess.school.markdown_data import markdown_blocks

SINGLE_COLUMN_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*\|?\s*$")


def _cells(line: str) -> list[str]:
    """이스케이프된 파이프는 셀 경계로 사용하지 않는다."""
    stripped = line.strip().removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped)]


def page_table_rows(document_id: str, pdf_page: int, page_text: str) -> tuple[list[dict], list[dict]]:
    """한 쪽의 행 후보와 검수 경고를 돌려준다. HTML 표는 자동 해석하지 않는다."""
    text, raw_fallback = clean_page(page_text)
    printed = extract_printed_page(text, document_id)
    blocks = markdown_blocks(text)
    result, warnings = [], []
    last_heading = ""
    table_no = 0
    for block in blocks:
        if block["kind"] == "heading":
            last_heading = clean_section_title(re.sub(r"^#{1,6}\s+", "", block["text"].strip()))
            continue
        if block["kind"] == "text":
            # 공통 파서는 2열 이상 파이프 표만 인식한다. 1열 표는 누락하지 않고 수동 검토로 남긴다.
            lines = block["text"].splitlines()
            for index in range(len(lines) - 1):
                if (
                    "|" in lines[index]
                    and len(_cells(lines[index])) == 1
                    and SINGLE_COLUMN_SEPARATOR.fullmatch(lines[index + 1])
                ):
                    table_no += 1
                    warnings.append({"pdf_page": pdf_page, "table": table_no, "reason": "1열 표 수동 검토"})
            continue
        if block["kind"] != "table":
            continue
        table_no += 1
        lines = block["text"].splitlines()
        if block["text"].lstrip().lower().startswith("<table"):
            warnings.append({"pdf_page": pdf_page, "table": table_no, "reason": "HTML 표 수동 검토"})
            continue
        header = lines[0].strip()
        headers = _cells(header)
        for row_no, line in enumerate(lines[2:], 1):
            row = line.strip()
            cells = _cells(row)
            # 변환 Markdown의 제목이 실제 PDF 표 제목과 같은지는 자동 확정할 수 없다.
            flags = ["PDF 표 제목·머리글·행 대조 필요"]
            if not last_heading:
                flags.append("표 제목 확인 필요")
            if len(cells) != len(headers):
                flags.append("열 개수 불일치")
            if any(not cell for cell in cells):
                flags.append("빈 셀·병합 여부 확인 필요")
            if any(token in row.lower() for token in ("rowspan", "colspan")):
                flags.append("병합 셀 수동 확인 필요")
            if raw_fallback:
                flags.append("보존용 원문 사용; PDF 표 구조 확인 필요")
            chunk_id = chunk_row_id(document_id, pdf_page, table_no, row_no)
            result.append({
                "chunk_id": chunk_id,
                "document_id": document_id,
                "pdf_page": pdf_page,
                "printed_page": printed,
                "section": last_heading,
                "text": "\n".join(part for part in (last_heading, header, row) if part),
                "table_title": last_heading,
                "header": header,
                "row_text": row,
                "review_flags": flags,
            })
    return result, warnings


def document_table_rows(document_id: str, markdown: str) -> tuple[list[dict], list[dict]]:
    """페이지 순서와 표 순서를 보존해 한 문서의 검수용 행 후보를 만든다."""
    headings = list(PAGE_HEADING.finditer(markdown))
    if not headings:
        raise ValueError("PDF 페이지 제목을 찾지 못했습니다.")
    rows, warnings = [], []
    seen_pages = set()
    for index, heading in enumerate(headings):
        page = int(heading.group(1))
        if page in seen_pages:
            raise ValueError(f"중복 PDF 페이지: {page}")
        seen_pages.add(page)
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        page_rows, page_warnings = page_table_rows(document_id, page, markdown[heading.end():end])
        rows.extend(page_rows)
        warnings.extend(page_warnings)
    return rows, warnings


def summarize(rows: list[dict], warnings: list[dict]) -> dict:
    """원문을 출력하지 않고 검수 규모만 계산한다."""
    flags = Counter(flag for row in rows for flag in row["review_flags"])
    return {
        "rows": len(rows),
        "pages": len({row["pdf_page"] for row in rows}),
        "rows_needing_review": sum(bool(row["review_flags"]) for row in rows),
        "review_reasons": dict(sorted(flags.items())),
        "skipped_tables": len(warnings),
    }
