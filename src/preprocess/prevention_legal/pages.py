"""담당 문서(md)에서 PDF 쪽 단위로 원문을 읽는 보조 도구 (유종혁 / prevention_legal).

설계의 본질이 아닌 '읽기 도구'만 담았다. 문서 md 는 `## PDF N페이지` 제목으로 쪽이 나뉜다.
"""
import re
from functools import cache
from pathlib import Path

# src/preprocess/prevention_legal/pages.py -> parents[3] 이 저장소 루트
RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "raw" / "prevention_legal"

# 짧은 이름 -> (document_id [임시], 파일 이름)
DOCS = {
    "law": ("moleg_infectious_disease_act_21521",
            "감염병의 예방 및 관리에 관한 법률(법률)(제21521호)(20261008).md"),
    "measles": ("kdca_measles_2026", "2026년도 홍역 대응 지침(전자용).md"),
    "hfmd": ("kdca_hfmd_2026", "★2026년도 엔테로바이러스 감염증·수족구병 관리지침(전자용).md"),
    "vaccine": ("kdca_vaccine_preventable_2026", "2026년도 예방접종 대상 감염병 관리 지침_최종.md"),
    # 법정감염병 진단·신고기준 (변환본, 237쪽). 원본 대조 결과는 06 노트북에 기록
    "diagnosis": ("kdca_diagnosis_reporting_2026", "2026년 법정감염병 진단·신고기준 지침_최종.md"),
}

_PAGE_HEADER = re.compile(r"^## PDF (\d+)페이지\s*$")


@cache
def _load_pages(key: str) -> dict[int, str]:
    """문서 하나를 {쪽번호: 쪽 글} 로 나눠 둔다."""
    _, filename = DOCS[key]
    pages: dict[int, list[str]] = {}
    current = None
    for line in (RAW_DIR / filename).read_text(encoding="utf-8").splitlines():
        m = _PAGE_HEADER.match(line)
        if m:
            current = int(m.group(1))
            pages[current] = []
        elif current is not None:
            pages[current].append(line)
    return {n: "\n".join(lines) for n, lines in pages.items()}


def read_page(key: str, page: int) -> str:
    """PDF 쪽 하나의 원문. 빈 줄 · 이미지 링크 줄 · 구분선은 뺀다."""
    text = _load_pages(key)[page]
    lines = [ln.rstrip() for ln in text.splitlines()
             if ln.strip() and not ln.startswith("![PDF") and ln.strip() != "---"]
    return "\n".join(lines)


def _squash(s: str) -> str:
    """공백·줄바꿈·마크다운 기호(*, #)와 대괄호, 표 안의 줄바꿈 태그(<br>)를 없앤 비교용 글. (간단한 '후보' 확인용)"""
    s = s.replace("<br>", "")  # 변환본 표 안의 줄바꿈 태그는 원문 비교에서 뺀다
    s = s.replace("○", "")     # 목록 기호(원·불릿)는 쪽마다 달라서 비교에서 뺀다
    return re.sub(r"[\s*#\[\]]+", "", s)


def excerpt_in_page(excerpt: str, key: str, page: int) -> bool:
    """발췌문이 그 쪽 원문에 실제로 있는지 (공백 · 줄바꿈 · ** · [ ] 차이는 무시하는 후보 확인).

    표의 다른 셀이나 줄이 이어져도 일치로 보일 수 있다. '확인'이 나와도 PDF 에서 문맥을 직접 대조한다.
    """
    q = _squash(excerpt)
    # 기호만 있는 발췌문(예: "○", "<br>")은 정규화하면 빈 글이 되므로 바로 거부한다
    return bool(q) and q in _squash(read_page(key, page))


def make_citation(key: str, page: int, excerpt: str, block: int = 1) -> dict:
    """계약 v1.2 의 Citation 한 건을 만든다. 잘못된 입력이면 ValueError (짧은 안내 문장).

    chunk_id 형식은 임시이다: <document_id>:p<PDF쪽 4자리>:b<번호 2자리>
    (팀이 형식을 확정하면 바꾼다. 번호는 3단계 청크 분할에서 정해지며 지금은 사람이 붙인다.
     지금의 chunk_id 는 연습용이며 실제 DB 의 청크를 확인한 것이 아니다.)
    같은 chunk_id 의 Citation 은 결과에 한 번만 담아야 한다.
    """
    if key not in DOCS:
        raise ValueError(f"문서 키가 없습니다: {key!r} (사용 가능: {', '.join(DOCS)})")
    if page not in _load_pages(key):
        raise ValueError(f"{key} 문서에 {page}쪽이 없습니다.")
    if not 1 <= block <= 99:
        raise ValueError("block 은 1~99 사이여야 합니다.")
    if not excerpt_in_page(excerpt, key, page):
        raise ValueError(f"발췌문을 {key} {page}쪽에서 찾지 못했습니다. 원문과 한 글자씩 비교해 보세요.")
    document_id, _ = DOCS[key]
    return {
        "document_id": document_id,
        "chunk_id": f"{document_id}:p{page:04d}:b{block:02d}",
        "pdf_page": page,
        "printed_page": None,
        "excerpt": excerpt,
    }


def make_table_citation(key: str, page: int, excerpt: str, table: int = 1, row: int = 1) -> dict:
    """표 행 하나의 Citation. chunk_id는 v3.1 형식 <document_id>:p<쪽4자리>:t<표2자리>r<행2자리>.

    표는 행 단위로 청크한다(스키마 v3.1 5-2절). 발췌문은 같은 쪽 원문에 있어야 한다.
    """
    if key not in DOCS:
        raise ValueError(f"문서 키가 없습니다: {key!r}")
    if page not in _load_pages(key):
        raise ValueError(f"{key} 문서에 {page}쪽이 없습니다.")
    if not 1 <= table <= 99 or not 1 <= row <= 99:
        raise ValueError("table·row 는 1~99 사이여야 합니다.")
    if not excerpt_in_page(excerpt, key, page):
        raise ValueError(f"발췌문을 {key} {page}쪽에서 찾지 못했습니다.")
    document_id, _ = DOCS[key]
    return {
        "document_id": document_id,
        "chunk_id": f"{document_id}:p{page:04d}:t{table:02d}r{row:02d}",
        "pdf_page": page,
        "printed_page": None,
        "excerpt": excerpt,
    }

