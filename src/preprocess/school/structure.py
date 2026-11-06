"""Read document structure before table deduplication or page selection.

Paths belong to source positions, never to the last heading of a page. No disease
inference is performed here. Repeated running chapter headers do not reset paths.
"""
from __future__ import annotations

import re

CAPTION = re.compile(r"^[\[<]표\s*(\d+[-–]\d+)[\]>]\s*(.*)")
TABLE_NUMBER = re.compile(r"표\s*(\d+[-–]\d+)")
CHAPTER = re.compile(r"^제\s*(\d+)\s*장\s*[|｜]?\s*(.*)")
RUNNING_CHAPTER = re.compile(r"^(.*?)\s+제\s*(\d+)\s*장$")
ROMAN = re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.．]?\s+\S")
PLAIN = re.compile(r"^\d+\s+[^\d\s]")
DOTTED = re.compile(r"^\d+\.\s+\S")
LETTER = re.compile(r"^([가나다라마바사아자차카타파하])\.\s+\S")
PAREN = re.compile(r"^\d+\)\s*\S")
LETTERS = "가나다라마바사아자차카타파하"


def plain_line(raw: str) -> str:
    return re.sub(r"^#{1,6}\s+", "", raw.strip().replace("**", ""))


def heading_kind(line: str) -> str | None:
    if len(line) > 140:
        return None
    if re.match(r"^\d{3,}[.\s]", line) or re.match(r"^\d+\s+\S+\s+[\d,]+$", line):
        return None  # years, country/count table rows
    for kind, pattern in (("roman", ROMAN), ("plain", PLAIN), ("number", DOTTED),
                          ("letter", LETTER), ("item", PAREN)):
        if pattern.match(line):
            return kind
    if re.match(r"^별지\s*\d+\s+", line):
        return "annex"
    return None


def document_structure(pages: dict[int, str]) -> dict[int, list[dict]]:
    """Return source-ordered entries with immutable path snapshots and captions.

    Korean lettering is reused below 1) in the ECP form. A sequence restart or
    return to an already open outer letter closes the inner list.
    """
    stack: list[tuple[int, str]] = []
    chapter_no = None
    result = {}
    for page, text in sorted(pages.items()):
        entries = []
        raw_lines = text.splitlines()
        for index, raw in enumerate(raw_lines):
            line = plain_line(raw)
            if line == '부록' and raw.lstrip().startswith('#'):
                stack, chapter_no = [(0, '부록')], 'appendix'
                entries.append({"line": line, "index": index, "path": tuple(stack), "heading": True,
                                    "furniture": False, "caption": None})
                continue
            chapter = CHAPTER.match(line)
            running = RUNNING_CHAPTER.match(line)
            if chapter or running:
                no, title = ((chapter.group(1), chapter.group(2)) if chapter
                             else (running.group(2), running.group(1)))
                if title and no != chapter_no:
                    stack = [(0, f"제{no}장 {title.strip()}")]
                    chapter_no = no
                entries.append({"line": line, "index": index, "path": tuple(stack), "heading": False,
                                    "furniture": True, "caption": None})
                continue
            caption = CAPTION.match(line)
            kind = heading_kind(line) if not caption else None
            # Numbered rows in appendix forms are table cells, not sections.
            if kind == "plain" and any(r == 15 for r, _ in stack):
                kind = None
            if kind:
                rank = {"roman": 10, "annex": 15, "plain": 20, "number": 30,
                        "letter": 40, "item": 50}[kind]
                if kind == "letter" and any(r == 50 for r, _ in stack):
                    inner = next((s for r, s in stack if r == 60), None)
                    outer = next((s for r, s in stack if r == 40), None)
                    letter = LETTER.match(line).group(1)
                    following = next((plain_line(s) for s in raw_lines[index + 1:]
                                      if plain_line(s)), "")
                    starts_outer_item_list = bool(re.match(r"^1\)", following))
                    if (not inner and letter == "가") or (inner and
                            LETTERS.index(LETTER.match(inner).group(1)) < LETTERS.index(letter)) or outer and LETTER.match(outer).group(1) >= letter:
                        rank = 60
                    if starts_outer_item_list:
                        rank = 40
                stack = [(r, s) for r, s in stack if r < rank] + [(rank, line)]
            entries.append({"line": line, "index": index, "path": tuple(stack), "heading": bool(kind),
                                "furniture": False, "caption": caption.group(1) if caption else None})
        result[page] = entries
    return result


def path_text(path) -> str:
    return " > ".join(s for _, s in path)


def table_context(entries: list[dict], title: str) -> dict | None:
    """Match a numbered table to its body caption; ambiguity is an error."""
    match = TABLE_NUMBER.search(title)
    if match:
        candidates = [e for e in entries if e["caption"] == match.group(1)]
    else:
        # Some source tables have descriptive captions without a table number.
        key = re.sub(r'[\W_]+', '', re.split(r'\s+[—–]\s+', title)[0])
        candidates = [e for e in entries if key and key == re.sub(r'[\W_]+', '', e['line'])]
    if len(candidates) > 1:
        raise ValueError(f"표 캡션 위치가 모호함: {title}")
    return candidates[0] if candidates else None
