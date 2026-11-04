"""manifest 선정 쪽수 전체를 공식 Chunk로 만든다 - 표 행 청크(표 작업본) + 본문 문단 청크(본문 변환본).

- 표 행: 표 작업본 Markdown의 표마다 데이터 행 하나 = 청크 하나. ID는 `p{쪽}:t{그 쪽 표 순서}r{행 순서}`
  (id_map.table_row_position과 같은 규칙 → 11/4 적재한 시나리오 표 4행과 같은 ID·본문이 나온다).
  text = 표 제목 + 머리글 + 행(온톨로지 5-2).
- 본문: 본문 변환본은 PDF 추출본이라 쪽마다 머리글·세로 장 표시·쪽 번호가 섞여 있다.
  (1) 문서 전체 쪽의 20% 이상에서 반복되는 짧은 줄(머리글·장 표시)을 자동으로 찾아 빼고,
  (2) 2글자 이하 조각 줄·쪽 번호·구분선을 빼고, (3) 제목 줄('1)', '가.', '[표 3-4]' 등)에서 끊어
  최대 약 800자 청크로 묶는다. ID는 `p{쪽}:b{쪽 안 순서}`. 원문 줄은 고치지 않고 줄 단위로 보존한다.
- 질병 연결(ABOUT)은 표 행에만 만든다: 행 첫 칸이 질병 사전과 정확히 일치하거나, 표 제목이
  '결핵 개요'처럼 한 질병으로 시작할 때. 본문 문단은 질병을 언급해도 연결하지 않는다(언급 ≠ 주제).
- 쪽 번호는 변환본 표시를 쓴다(사용자가 PDF와 대조 완료, 11/4).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

from index.school.ids_local import chunk_block_id, chunk_row_id
from index.school.normalize import resolve_mention
from preprocess.school.markdown_data import annotate_table_blocks, markdown_blocks

SOURCES = {
    "moe_school_manual_v3": {
        "body": "data/raw/학교 감염병 예방·위기대응 매뉴얼 제3차 개정판(초중고·특수학교용).md",
        "tables": "data/raw/학교_위기대응_매뉴얼_본문_표.md", "source_id": "school_manual:tables",
        "offset": 14},
    "gyeonggi_school_guide_2025": {
        "body": "data/raw/(본책) 경기도 학교 감염병관리 실무가이드북.md",
        "tables": "data/raw/경기도_실무가이드북_본문_표.md", "source_id": "gyeonggi_guide:tables",
        "offset": 10},
}
BODY_PAGE = re.compile(r"^## PDF (\d+)페이지\s*$", re.MULTILINE)
TITLE_PREFIX = re.compile(
    r"^PDF\s*\d+(?:\s*[~～-]\s*\d+)?\s*(?:쪽|페이지)\s*"
    r"(?:\(\s*인쇄\s*\d+(?:\s*[~～-]\s*\d+)?\s*쪽\s*\)|·\s*인쇄\s*\d+(?:\s*[~～-]\s*\d+)?\s*쪽)?\s*·\s*")
PRINTED = re.compile(r"인쇄\s*(\d+)")
HEADING = re.compile(r"^(?:\d+\)|\d+\.?\s|[가-하]\.\s|【|\[표\s?\d|<표\s?\d|[□■○●❶❷❸]|제\d+장|[ⅠⅡⅢⅣⅤⅥ]\s)")
LEVEL0 = re.compile(r"^[ⅠⅡⅢⅣⅤⅥ]\s+\S")           # 'Ⅲ 등교중지'
LEVEL1 = re.compile(r"^\d+\.?\s+[^\d\s]")           # '2. 대응 제1단계…', '1 등교중지의 원칙과 절차'
LEVEL2 = re.compile(r"^[가-하]\.\s+\S")             # '가. 개요'
MAX_CHARS = 800
MIN_CHARS = 200
MIN_FRAGMENT = 60


def selected_pages(spec: str) -> list[int]:
    """manifest 선정쪽수 '36 38 53~68' → [36, 38, 53, ..., 68]."""
    pages: set[int] = set()
    for part in spec.split():
        if "~" in part:
            start, end = (int(x) for x in part.split("~"))
            pages |= set(range(start, end + 1))
        else:
            pages.add(int(part))
    return sorted(pages)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 표 행

def clean_table_title(title: str) -> str:
    return TITLE_PREFIX.sub("", (title or "").strip()).strip()


def _first_cell(row: str) -> str:
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    return cells[0] if cells else ""


def table_row_chunks(document_id: str, tables_text: str, source_id: str, pages: set[int],
                     offset: int, books: dict, source_path: str) -> tuple[list[dict], dict[str, list[str]]]:
    """반환: (청크 목록, {chunk_id: [disease_id]})."""
    blocks = annotate_table_blocks(markdown_blocks(tables_text), source_id)
    tables = [b for b in blocks if b["kind"] == "table" and b["pdf_page"] in pages]
    chunks, about = [], {}
    by_page: Counter = Counter()
    for block in sorted(tables, key=lambda b: b["start_offset"]):
        by_page[block["pdf_page"]] += 1
        table_no = by_page[block["pdf_page"]]
        title = clean_table_title(block["table_title"])
        match = PRINTED.search(block["table_title"] or "")
        printed = int(match.group(1)) if match else block["pdf_page"] - offset
        if printed != block["pdf_page"] - offset:
            raise ValueError(f"인쇄 쪽 환산 불일치: {document_id} PDF {block['pdf_page']}")
        lines = block["text"].splitlines(keepends=True)
        header = lines[0]
        title_disease = resolve_mention(title.split()[0] if title else "", books["disease"], "disease_id")
        row_no = 0
        for line in lines[2:]:
            if not line.strip():
                continue
            row_no += 1
            chunk_id = chunk_row_id(document_id, block["pdf_page"], table_no, row_no)
            chunks.append({
                "chunk_id": chunk_id, "document_id": document_id,
                "text": f"{title}\n{header}{line}".rstrip("\n"), "pdf_page": block["pdf_page"],
                "printed_page": printed, "section": title, "kind": "table_row",
                "temp_chunk_id": None, "row_text": line, "source_path": source_path,
                "content_hash": sha(tables_text)})
            row_disease = resolve_mention(_first_cell(line), books["disease"], "disease_id")
            for result in (row_disease, title_disease):
                if result["status"] == "RESOLVED":
                    about[chunk_id] = [result["id"]]
                    break
    return chunks, about


# ---------------------------------------------------------------- 본문 문단

def split_body_pages(body_text: str) -> dict[int, str]:
    parts = BODY_PAGE.split(body_text)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}


def repeated_lines(pages: dict[int, str], share: float = 0.2, tab_share: float = 0.05) -> set[str]:
    """문서 전체 쪽의 share 이상에 나오는 짧은 줄(머리글), 또는 tab_share 이상에 나오는 아주 짧은 줄
    (장마다 바뀌는 옆 탭 '제3장/학교 내/발생 시').
    """
    counts: Counter = Counter()
    for text in pages.values():
        counts.update({line.strip().replace("**", "") for line in text.splitlines()
                       if 0 < len(line.strip()) <= 60})
    # 번호가 붙은 제목('가. 개요')은 여러 장에 반복돼도 내용이므로 잡음으로 보지 않는다.
    return {line for line, n in counts.items() if not HEADING.match(line)
            and (n >= max(3, share * len(pages)) or (len(line) <= 40 and n >= max(3, tab_share * len(pages))))}


def squash(text: str) -> str:
    """비교용 문자열: <br>·공백·표 구분자·글머리표·문장부호를 뺀다(본문 '- '와 표 ';' 차이 무시)."""
    return re.sub(r"<br\s*/?>|[\s|\-–∙•‣¢·;:,.()\[\]<>「」『』※]", "", text)


def clean_body_lines(text: str, noise: set[str], table_text: str = "") -> list[str]:
    """table_text: 같은 쪽의 정리된 표 작업본 내용(squash). 본문 변환본 속 뒤섞인 표 조각은 이미
    표 행 청크로 들어가므로, 그 표에 그대로 있는 줄(6글자 이상)은 문단에서 뺀다."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip().replace("**", "")
        if (not line or line in noise or len(line) <= 2 or line == "---" or line.startswith("![")
                or re.fullmatch(r"\d{1,3}", line) or re.fullmatch(r"제\s*\d+\s*장", line)):
            continue
        key = squash(line)
        if table_text and (len(key) >= 3 and key in table_text or covered(key, table_text) >= 0.6):
            continue
        lines.append(line)
    return lines


def covered(key: str, table_text: str, n: int = 4) -> float:
    """줄의 n글자 조각 중 표 텍스트에 있는 비율. 줄바꿈으로 끊긴 표 조각은 높고, 표에 없는 내용은 낮다."""
    if len(key) < 8:
        return 0.0
    grams = [key[i:i + n] for i in range(len(key) - n + 1)]
    return sum(g in table_text for g in grams) / len(grams)


def body_chunks(document_id: str, body_text: str, pages: list[int], offset: int,
                source_path: str, table_text_by_page: dict[int, str] | None = None
                ) -> tuple[list[dict], dict[int, str]]:
    """반환: (문단 청크, {쪽: 그 쪽에서 유효한 장·절 경로}). 절 경로는 표 행 청크의 section에도 쓴다."""
    table_text_by_page = table_text_by_page or {}
    all_pages = split_body_pages(body_text)
    noise = repeated_lines(all_pages)
    chunks, level1, level2, previous = [], None, None, None
    page_sections: dict[int, str] = {}
    for page in pages:
        if previous is not None and page != previous + 1:
            level1 = level2 = None        # 이어지지 않는 쪽으로 넘어가면 앞 절 제목을 끌고 오지 않는다
        previous = page
        lines = clean_body_lines(all_pages.get(page, ""), noise, table_text_by_page.get(page, ""))
        groups, current = [], []
        for line in lines:
            size = sum(len(x) + 1 for x in current)
            if current and ((HEADING.match(line) and size >= MIN_CHARS) or size + len(line) > MAX_CHARS):
                groups.append(current)
                current = []
            current.append(line)
        if current:
            groups.append(current)
        # 너무 짧은 조각(MIN_FRAGMENT 미만)은 앞 묶음에 붙인다. 쪽에 하나뿐이면 버리지 않는다.
        merged: list[list[str]] = []
        for group in groups:
            if merged and sum(len(x) for x in group) < MIN_FRAGMENT:
                merged[-1] += group
            else:
                merged.append(group)
        groups = [g for g in merged if sum(len(x) for x in g) >= MIN_FRAGMENT
                  or (len(merged) == 1 and sum(len(x) for x in g) >= 20)]   # 20자 미만 조각('유무)')은 버림
        for number, group in enumerate(groups, 1):
            for line in group:
                if (LEVEL0.match(line) and len(line) <= 40) or (LEVEL1.match(line) and len(line) <= 60):
                    level1, level2 = line, None
                elif LEVEL2.match(line) and len(line) <= 60:
                    level2 = line
            section = " > ".join(x for x in (level1, level2) if x) or f"PDF {page}쪽 본문"
            text = "\n".join(group)
            chunks.append({
                "chunk_id": chunk_block_id(document_id, page, number), "document_id": document_id,
                "text": text, "pdf_page": page, "printed_page": page - offset, "section": section,
                "kind": "paragraph", "temp_chunk_id": None, "row_text": text,
                "source_path": source_path, "content_hash": sha(body_text)})
        for line in lines:          # 버려진 조각 안의 제목도 절 경로에는 반영한다
            if (LEVEL0.match(line) and len(line) <= 40) or (LEVEL1.match(line) and len(line) <= 60):
                level1, level2 = line, None
            elif LEVEL2.match(line) and len(line) <= 60:
                level2 = line
        page_sections[page] = " > ".join(x for x in (level1, level2) if x)
    return chunks, page_sections


# ---------------------------------------------------------------- 전체

def build_all_chunks(root: str | Path, manifest: dict[str, dict], books: dict
                     ) -> tuple[list[dict], dict[str, list[str]]]:
    """두 학교 문서의 선정 쪽수 전체 청크와 ABOUT 매핑."""
    root = Path(root)
    chunks, about = [], {}
    for document_id, spec in SOURCES.items():
        pages = selected_pages(manifest[document_id]["선정쪽수"])
        tables_text = (root / spec["tables"]).read_text(encoding="utf-8-sig")
        body_text = (root / spec["body"]).read_text(encoding="utf-8-sig")
        rows, row_about = table_row_chunks(document_id, tables_text, spec["source_id"], set(pages),
                                           spec["offset"], books, spec["tables"])
        chunks += rows
        about.update(row_about)
        by_page: dict[int, str] = {}
        for row in rows:
            by_page[row["pdf_page"]] = by_page.get(row["pdf_page"], "") + squash(row["text"])
        paragraphs, page_sections = body_chunks(document_id, body_text, pages, spec["offset"],
                                                spec["body"], by_page)
        # 표 행 section = 본문 장·절 경로 > 표 제목 (온톨로지 ② section '장·절 경로'). 표 제목에 단계가
        # 없어도('[표 3-11] 능동감시 실시 담당자별 역할') 절 경로로 원문 단계를 알 수 있다.
        for row in rows:
            path = page_sections.get(row["pdf_page"])
            if path:
                row["section"] = f"{path} > {row['section']}"
        chunks += paragraphs
    ids = [c["chunk_id"] for c in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("청크 ID 중복")
    return chunks, about
