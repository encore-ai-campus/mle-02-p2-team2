"""manifest 선정 쪽수 전체를 공식 Chunk로 만든다 - 표 행 청크(표 작업본) + 본문 문단 청크(본문 변환본).

- 표 행: 표 작업본 Markdown의 표마다 데이터 행 하나 = 청크 하나. ID는 `p{쪽}:t{그 쪽 표 순서}r{행 순서}`
  (id_map.table_row_position과 같은 규칙 → 11/4 적재한 시나리오 표 4행과 같은 ID·본문이 나온다).
  text = 표 제목 + 머리글 + 행(온톨로지 5-2).
- 본문: 전체 페이지의 구조를 먼저 읽고 선정 페이지의 내용만 출력한다. 장·절·항의
  위치별 경로를 보존하고 절(가.·1. 이상) 경계에서 분할한 뒤 최대 800자로 묶는다. 짧다는 이유로
  서로 다른 절을 합치거나 문단을 버리지 않는다. 절 아래 `1)` 항목은 200자까지 함께 묶는다(v4).
  표 캡션은 본문 참조로도 보존한다.
- 표 제목은 본문의 해당 표 번호 캡션을 우선하며, 작업본 제목은 추적 정보로 분리한다.
  변환 순서가 뒤집힌 검증 사례는 school_layout_corrections.json으로 명시한다.
- 질병 연결(ABOUT)은 표 행에만 만든다: 행 첫 칸이 질병 사전과 정확히 일치하거나, 표 제목이
  '결핵 개요'처럼 한 질병으로 시작할 때. 본문 문단은 질병을 언급해도 연결하지 않는다(언급 ≠ 주제).
- 쪽 번호는 변환본 표시를 쓴다(사용자가 PDF와 대조 완료, 11/4).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from index.school.ids_local import chunk_block_id, chunk_row_id
from index.school.normalize import resolve_mention
from preprocess.school.markdown_data import annotate_table_blocks, markdown_blocks
from preprocess.school.reviewed_context import apply_reviewed_tables, load_review
from preprocess.school.structure import (
    CAPTION,
    TABLE_NUMBER,
    document_structure,
    heading_kind,
    path_text,
    plain_line,
    table_context,
)

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
MAX_CHARS = 800
# 11/06 v4: `1)`·하위 `가.`(rank ≥ 50) 항목은 같은 상위 절(rank < 50) 안에서 MIN_CHARS까지 묶는다.
# v3는 항목마다 청크를 끊어 본문 1,156개 중 713개가 80자 미만이 됐고, 맥락 없는 조각이
# 검색 상위를 차지했다(서비스DB 이행 평가 Hybrid Hit@5 19→13/34). 상위 절 경계는 그대로 지킨다.
ITEM_RANK = 50
MIN_CHARS = 200
CHUNKER_VERSION = "school_structure_v4_item_merge"


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
    """내용 줄만 표 중복 필터에 통과시킨다. 제목·표 참조는 항상 보존한다.

    table_text는 같은 쪽의 표 작업본을 squash한 비교 문자열이다.
    """
    lines = []
    for raw in text.splitlines():
        line = plain_line(raw)
        if (not line or line in noise or len(line) <= 2 or line == "---" or line.startswith("![")
                or re.fullmatch(r"\d{1,3}", line) or re.fullmatch(r"제\s*\d+\s*장", line)):
            continue
        key = squash(line)
        # Structure and explicit table references must survive content deduplication.
        if not (heading_kind(line) or CAPTION.match(line)) and table_text and (
                len(key) >= 3 and key in table_text or covered(key, table_text) >= 0.6):
            continue
        lines.append(line)
    return lines


def covered(key: str, table_text: str, n: int = 4) -> float:
    """줄의 n글자 조각 중 표 텍스트에 있는 비율. 줄바꿈으로 끊긴 표 조각은 높고, 표에 없는 내용은 낮다."""
    if len(key) < 8:
        return 0.0
    grams = [key[i:i + n] for i in range(len(key) - n + 1)]
    return sum(g in table_text for g in grams) / len(grams)


def _scope(path) -> tuple:
    return tuple(p for p in path if p[0] < ITEM_RANK)


def _common_path(paths: list[tuple]) -> tuple:
    """Section of a merged chunk = the deepest path shared by all of its lines."""
    common = paths[0]
    for path in paths[1:]:
        n = 0
        while n < min(len(common), len(path)) and common[n] == path[n]:
            n += 1
        common = common[:n]
    return common


def body_chunks(document_id: str, body_text: str, pages: list[int], offset: int,
                source_path: str, table_text_by_page: dict[int, str] | None = None,
                *, structure: dict | None = None) -> tuple[list[dict], dict[int, str]]:
    """Split within a structural scope, never across sibling headings.

    The second result remains a page summary for old callers; table attribution
    uses caption positions in build_all_chunks, not this lossy summary.
    """
    table_text_by_page = table_text_by_page or {}
    all_pages = split_body_pages(body_text)
    structure = structure if structure is not None else document_structure(all_pages)
    noise = repeated_lines(all_pages)
    chunks, page_sections = [], {}
    for page in pages:
        groups, current, paths, current_scope, heads_only = [], [], [], None, True

        def close(groups=groups):
            nonlocal current, paths, heads_only
            if current:
                groups.append((current, _common_path(paths)))
            current, paths, heads_only = [], [], True

        for entry in structure.get(page, []):
            line, path = entry['line'], entry['path']
            if entry['furniture'] or not clean_body_lines(
                    line, noise, table_text_by_page.get(page, '')):
                continue
            # Sibling/outer sections (rank < ITEM_RANK) are always a boundary and
            # are never merged. Items below them merge until MIN_CHARS so a
            # single `4) ...` line keeps its neighbouring context. Captions
            # remain with their introduction; no fragment is discarded.
            scope = _scope(path)
            size = len('\n'.join(current))
            item_heading = entry['heading'] and path and path[-1][0] >= ITEM_RANK
            # A heading with no body of its own (e.g. `2. 대응 제1단계` directly
            # followed by `가. ...`) opens its first child instead of standing alone.
            # Runs of body-less sibling headings (disease lists, legal enumerations)
            # form one list chunk up to MIN_CHARS; siblings with their own body
            # text still start a new chunk.
            descends = (current_scope is not None and len(scope) > len(current_scope)
                        and scope[:len(current_scope)] == current_scope)
            sibling_list = (current_scope is not None and entry['heading'] and size < MIN_CHARS
                            and len(scope) == len(current_scope) and scope[:-1] == current_scope[:-1])
            boundary = ((scope != current_scope and not (heads_only and (descends or sibling_list)))
                        or (item_heading and size >= MIN_CHARS))
            if current and (boundary or size + len(line) + 1 > MAX_CHARS):
                close()
            current_scope = scope
            # `1)` items are content, not section headings.
            heads_only = heads_only and bool((entry['heading'] and not item_heading) or entry['caption'])
            # Long extraction lines still need a bounded chunk. Keep exact
            # substrings so the original characters can be recovered.
            while len(line) > MAX_CHARS:
                close()
                groups.append(([line[:MAX_CHARS]], path))
                line = line[MAX_CHARS:]
            if line:
                current.append(line)
                paths.append(path)
        close()
        for number, (group, path) in enumerate(groups, 1):
            text = '\n'.join(group)
            chunks.append({
                'chunk_id': chunk_block_id(document_id, page, number), 'document_id': document_id,
                'text': text, 'pdf_page': page, 'printed_page': page - offset,
                'section': path_text(path) or f'PDF {page}쪽 본문',
                'kind': 'paragraph', 'temp_chunk_id': None, 'row_text': text,
                'source_path': source_path, 'content_hash': sha(body_text)})
        entries = structure.get(page, [])
        page_sections[page] = path_text(entries[-1]['path']) if entries else ''
    return chunks, page_sections


def _attach_table_context(rows: list[dict], structure: dict, corrections: list[dict],
                          document_id: str) -> None:
    for row in rows:
        entries = structure.get(row['pdf_page'], [])
        entry = table_context(entries, row['section'])
        if entry:
            title = entry['line']
            path = entry['path']
            # A checklist caption belongs to its subsection, not the last
            # numbered eligibility condition preceding the table.
            if '역학조사' in title:
                path = tuple((r, s) for r, s in path if r < 50)
            status = 'body_caption'
        else:
            # Unnumbered working tables (e.g. disease overview) have no caption.
            # Prefer the page's opening overview heading, never its final heading.
            overview = next((e for e in entries if e['line'] == '가. 개요'), None)
            first = next((e for e in entries if e['heading']), None)
            if first is None:
                first = next((e for e in entries if not e['furniture'] and e['line']), None)
            path = (overview or first or {'path': ()})['path']
            if not overview and first:
                # Without a caption anchor, claim only the common enclosing
                # scope, rather than inventing a last/first subsection match.
                paths = [e['path'] for e in entries if e['index'] >= first['index']
                         and not e['furniture'] and not e['heading'] and e['line']]
                if paths:
                    common = list(paths[0])
                    for other in paths[1:]:
                        common = common[:next((i for i, (a, b) in enumerate(zip(common, other))
                                               if a != b), min(len(common), len(other)))]
                    path = tuple(common)
            title = re.split(r'\s+[—–]\s+', row['section'])[0]
            status = 'page_common_scope_fallback'
        number = TABLE_NUMBER.search(title)
        for correction in corrections:
            if (correction['document_id'] == document_id
                    and correction['pdf_page'] == row['pdf_page']
                    and number and correction['table_number'] == number.group(1)):
                subsection = correction['subsection']
                if not any(e['line'] == subsection for e in entries):
                    raise ValueError(f'보정 소제목을 원문에서 찾지 못함: {correction}')
                path = tuple((r, s) for r, s in path if r < 40) + ((40, subsection),)
                status = 'reviewed_layout_correction'
        working_title = row['section']
        row['working_table_title'] = working_title
        row['context_status'] = status
        row['context_verified'] = status != 'page_common_scope_fallback'
        row['text'] = title + '\n' + row['text'].split('\n', 1)[1]
        row['section'] = ' > '.join(x for x in (path_text(path), title) if x)


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
        structure = document_structure(split_body_pages(body_text))
        correction_file = root / 'data/specs/school_layout_corrections.json'
        corrections = (json.loads(correction_file.read_text(encoding='utf-8'))['tables']
                       if correction_file.exists() else [])
        _attach_table_context(rows, structure, corrections, document_id)
        apply_reviewed_tables(rows, load_review(root))
        paragraphs, _ = body_chunks(document_id, body_text, pages, spec['offset'],
                                    spec['body'], by_page, structure=structure)
        chunks += paragraphs
    ids = [c["chunk_id"] for c in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("청크 ID 중복")
    for chunk in chunks:
        chunk['chunker_version'] = CHUNKER_VERSION
    return chunks, about
