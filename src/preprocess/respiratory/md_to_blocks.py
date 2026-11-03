"""호흡기 지침 Markdown을 페이지별 JSONL 블록으로 변환한다."""

import json
import re
import sys
import unicodedata
from pathlib import Path

# 파일명에 포함된 문구와 문서 ID의 대응을 한곳에서 관리한다.
DOCUMENT_IDS = {
    "호흡기감염병 관리지침": "kdca_respiratory_2026",
    "시설별 인플루엔자": "kdca_flu_facility_2026",
    "코로나19": "kdca_covid19_2026",
}
EXPECTED_PAGE_OFFSETS = {
    "kdca_respiratory_2026": 20,
    "kdca_flu_facility_2026": 4,
}
COVID_DOCUMENT_ID = "kdca_covid19_2026"

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INPUT_DIR = PROJECT_ROOT / "data" / "raw" / "respiratory"
OUTPUT_PATH = PROJECT_ROOT / "data" / "interim" / "respiratory" / "blocks.jsonl"

PAGE_HEADING = re.compile(r"^##[ \t]+PDF[ \t]+(\d+)페이지[ \t]*$", re.MULTILINE)
RAW_HEADING = re.compile(r"^###[ \t]+보존용 원문 추출 텍스트[ \t]*$", re.MULTILINE)
REMOVED_HEADING = re.compile(r"^###[ \t]+(?:이미지 문자 인식\(OCR\)|원본 대조 수정)[ \t]*$")
SECTION_HEADING = re.compile(r"^#{1,4}[ \t]+(.+?)\s*$")
IMAGE_LINK = re.compile(r"!\[[^\]]*\]\((?:<[^>\n]*>|(?:[^()\n]|\([^()\n]*\))*)\)")
HTML_COMMENT = re.compile(r"<!--[\s\S]*?-->")
PRINTED_PAGE = re.compile(
    r"^(?:www\.kdca\.go\.kr\s*\[\s*[｜|]\s*\]\s*(\d+)"
    r"|[｜|]\s*(\d+)|(\d+)\s*[｜|]?)$"
)
FALLBACK_SECTION = re.compile(r"^(?:\d+|[가-힣])\s+\S.*$")
EMPTY_OCR_LINE = re.compile(r"^\(인식된 텍스트 없음[^\n]*\)\s*$", re.MULTILINE)


def make_chunk_id(document_id, pdf_page, block_no):
    """블록 ID 생성 규칙을 이 함수에 모아 변경하기 쉽게 한다."""
    return f"{document_id}:p{pdf_page:04d}:b{block_no:02d}"


def clean_page(page_text):
    """보존용 원문이 있으면 그것만 선택하고 지정된 표시를 정리한다."""
    raw_heading = RAW_HEADING.search(page_text)
    has_raw_fallback = raw_heading is not None
    if raw_heading:
        page_text = page_text[raw_heading.end():]

    page_text = HTML_COMMENT.sub("", page_text)
    page_text = IMAGE_LINK.sub("", page_text)
    lines = []
    for line in page_text.splitlines():
        line = line.strip()
        if line == "---" or REMOVED_HEADING.fullmatch(line):
            continue
        if line or (lines and lines[-1]):
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines), has_raw_fallback


def extract_printed_page(text, document_id):
    """쪽 끝에서부터 쪽번호 형식의 첫 줄을 찾는다."""
    if document_id == COVID_DOCUMENT_ID:
        return None
    for line in reversed(text.splitlines()):
        match = PRINTED_PAGE.fullmatch(line.strip())
        if match:
            return int(next(group for group in match.groups() if group is not None))
    return None


def clean_section_title(title):
    """제목의 표시 문자를 정리하되 본문 text는 바꾸지 않는다."""
    title = title.replace("**", "").replace("\x60", "").replace("\ufffd", " ")
    return " ".join(title.split())


def extract_section(text):
    lines = text.splitlines()
    for line in lines:
        match = SECTION_HEADING.match(line)
        if match:
            return clean_section_title(match.group(1))
    # 일반 제목이 없을 때만 숫자·가나다로 시작하는 제목을 찾는다.
    for line in lines:
        title = re.sub(r"^#{5,6}\s+", "", line)
        title = clean_section_title(title)
        if FALLBACK_SECTION.fullmatch(title):
            return title
    return ""


def has_content(text):
    """쪽번호·기호 또는 OCR 실패 표시만 있는 페이지를 제외한다."""
    text = EMPTY_OCR_LINE.sub("", text)
    return re.search(r"[^\d\s｜|·-]", text) is not None


def input_files():
    """누락·중복·미등록 파일을 확인해 문서가 잘못 매칭되는 일을 막는다."""
    paths = sorted(INPUT_DIR.glob("*.md"))
    matched = {}
    for path in paths:
        # macOS에서 분리된 한글 파일명도 같은 문서명으로 비교한다.
        filename = unicodedata.normalize("NFC", path.name)
        matches = [doc_id for name, doc_id in DOCUMENT_IDS.items() if name in filename]
        if len(matches) != 1:
            raise ValueError(f"문서 ID를 하나로 정할 수 없는 파일: {path.name}")
        document_id = matches[0]
        if document_id in matched:
            raise ValueError(f"문서 ID가 중복된 파일: {matched[document_id].name}, {path.name}")
        matched[document_id] = path

    missing = [name for name, doc_id in DOCUMENT_IDS.items() if doc_id not in matched]
    if missing:
        raise ValueError(f"입력 MD가 없는 문서: {', '.join(missing)} (경로: {INPUT_DIR})")
    return matched


def main():
    files = input_files()
    blocks = []
    summaries = []
    skipped = []
    warnings = []

    for document_id, path in files.items():
        content = path.read_text(encoding="utf-8")
        headings = list(PAGE_HEADING.finditer(content))
        if not headings:
            raise ValueError(f"PDF 페이지 제목을 찾지 못한 파일: {path.name}")
        page_numbers = set()
        block_count = 0
        printed_count = 0

        for index, heading in enumerate(headings):
            pdf_page = int(heading.group(1))
            if pdf_page in page_numbers:
                raise ValueError(f"PDF 페이지 번호 중복: {path.name}, {pdf_page}")
            page_numbers.add(pdf_page)
            end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
            text, has_raw_fallback = clean_page(content[heading.end():end])
            printed_page = extract_printed_page(text, document_id)
            if printed_page is not None:
                printed_count += 1
            if not has_content(text):
                skipped.append(f"{document_id}: PDF {pdf_page}페이지")
                continue

            expected = EXPECTED_PAGE_OFFSETS.get(document_id)
            if printed_page is not None and expected is not None and pdf_page - printed_page != expected:
                warnings.append(
                    f"{document_id}: PDF {pdf_page} / 인쇄 {printed_page} "
                    f"(차이 {pdf_page - printed_page}, 기대 {expected})"
                )
                printed_page = None
            blocks.append({
                "chunk_id": make_chunk_id(document_id, pdf_page, 1),
                "document_id": document_id,
                "text": text,
                "pdf_page": pdf_page,
                "printed_page": printed_page,
                "section": extract_section(text),
                "has_raw_fallback": has_raw_fallback,
            })
            block_count += 1
        summaries.append((document_id, len(headings), block_count, printed_count))

    # 모든 입력 확인이 끝난 뒤에만 출력 파일을 만든다.
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as output:
        for block in blocks:
            output.write(json.dumps(block, ensure_ascii=False) + "\n")

    for document_id, pages, count, printed_count in summaries:
        print(f"{document_id}: 페이지 {pages}개, 블록 {count}개")
        print(
            f"  인쇄 쪽 있음 {printed_count}쪽 / 없음 {pages - printed_count}쪽"
            " (건너뛴 쪽 포함)"
        )
    print("skipped 페이지:")
    for item in skipped or ["없음"]:
        print(f"  {item}")
    print("printed_page 경고:")
    for item in warnings or ["없음"]:
        print(f"  {item}")
    print(f"출력: {OUTPUT_PATH}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, UnicodeError, ValueError) as error:
        print(f"오류: {error}", file=sys.stderr)
        sys.exit(1)
