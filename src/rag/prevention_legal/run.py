"""PreventionLegalGraph 질의 진입점 (유종혁 / prevention_legal) - 뼈대와 설명.

이 파일의 역할
- 질문 하나를 받아, 원문에서 찾은 근거를 계약 형식(QueryOutput)으로 돌려준다.
- 지금은 "고정 샘플"만 돌려준다. 실제 검색은 11/4 이후 단계에서 붙인다.
- 결과 형식이 맞는지는 contract_local.py 의 validate_query_output 이 마지막에 검사한다.

전체 흐름 (위에서 아래로 읽으면 된다)
  run(질문)
    1) 질문이 샘플과 같은지 확인
    2) 카드(주체·행동·조건·출처)에서 Citation 만들기   ← 근거 문장
    3) 카드에서 Finding 만들기                          ← 주장과 근거 번호 연결
    4) action_ids · warnings · answer 채우기
    5) validate_query_output 으로 최종 검사

빈칸은 모두 채웠다. 아래 설명은 흐름을 이해하기 위한 것이다.
Neo4j 에는 아직 쓰지 않는다. 테스트 적재는 접두사·테스트 라벨을 붙이고, 끝나면 삭제한다.
"""
import json
import re
from pathlib import Path

from preprocess.prevention_legal.pages import DOCS, make_citation
from rag.prevention_legal.contract_local import QueryInput, QueryOutput, validate_query_output

# ---------------------------------------------------------------------------
# 1) 고정 샘플 (사용자가 Part A 에서 정한 값을 넣는다)
# ---------------------------------------------------------------------------

# 샘플 질문. 요청 질문이 여기와 같을 때만 샘플 결과를 돌려준다.
SAMPLE_QUESTION = "홍역 의심환자가 생겼을 때 학교 담당교사와 의료기관은 각각 보건소에 무엇을 신고하거나 안내하나요? 법률과 지침의 조건을 구분해 주세요."

# 카드 한 건 = 학교 행동 한 개.
# "출처" 는 (문서 키, PDF 쪽, 원문 구절) 세 칸짜리 튜플이다.
#   - 문서 키는 pages.py 의 DOCS 키다 (예: "measles").
#   - 원문 구절은 블록 원문에 그대로 있어야 한다 (make_citation 은 쪽 안에서, build_citations 는 블록 안에서 확인한다).
# "블록" 은 그 행동 근거가 들어 있는 블록 번호(정수)다. 블록 파일의 chunk_id 끝 두 자리와 같다.
#   예) 93쪽 b01 이면 1, 93쪽 b03 이면 3.
# 예시 모양 (값은 직접 채운다):
#   {"주체": ..., "행동": ..., "조건": ..., "역할근거": ..., "basis": ..., "단계": ...,
#    "출처": ("measles", 93, "원문 구절"), "블록": 1}
SAMPLE_CARDS: list[dict] = [
    {"주체": "학교[담당교사]", "행동": "의료기관에서 진료 받도록 안내", "조건": "교내 홍역(의심)환자 발생 시",
     "역할근거": "가. 초 중 고등학교 내 환자 발생 시 학교[담당교사] 역할", "basis": "EXPLICIT", "단계": "UNSPECIFIED",
     "출처": ("measles", 93, "교내 홍역(의심)환자 발생 시 반드시 의료기관에서 진료 받도록 안내하고, 관할 보건소에 신고"), "블록": 1},
    {"주체": "학교[담당교사]", "행동": "관할 보건소에 신고", "조건": "교내 홍역(의심)환자 발생 시",
     "역할근거": "가. 초 중 고등학교 내 환자 발생 시 학교[담당교사] 역할", "basis": "EXPLICIT", "단계": "UNSPECIFIED",
     "출처": ("measles", 93, "교내 홍역(의심)환자 발생 시 반드시 의료기관에서 진료 받도록 안내하고, 관할 보건소에 신고"), "블록": 1},
]


# ---------------------------------------------------------------------------
# 2) 보조 함수 (흐름의 각 단계를 함수로 나눈 것)
# ---------------------------------------------------------------------------

def is_sample_question(request: QueryInput) -> bool:
    """요청 질문이 샘플 질문과 같은지 본다.
    설명: 샘플 결과는 샘플 질문에만 맞는 답이다. 다른 질문에 같은 답을 주면 안 된다.
    """
    return request["question"].strip() == SAMPLE_QUESTION.strip()




# 블록 파일 (블록 노트북 02 가 만드는 로컬 파일, Git 에는 올라가지 않는다)
BLOCKS_FILE = Path(__file__).resolve().parents[3] / "data" / "interim" / "prevention_legal" / "blocks.jsonl"


def _compact(text: str) -> str:
    """비교용 글: 공백, 별표(*), 대괄호를 뺀다. 강조 표시 차이는 무시한다."""
    return re.sub(r"[\s*\[\]]", "", text)


def _in_block(chunk_id: str, quote: str) -> bool:
    """구절이 chunk_id 블록의 원문 안에 있는지 본다."""
    for line in BLOCKS_FILE.read_text(encoding="utf-8").splitlines():
        b = json.loads(line)
        if b["chunk_id"] == chunk_id:
            return _compact(quote) in _compact(b["text"])
    raise ValueError(f"블록 파일에서 {chunk_id} 를 찾지 못했습니다.")


# 검색어 (임시 키워드 방식). 질문의 핵심어를 블록 글과 소제목에서 센다.
# 공동 DB 인덱스는 쓰지 않는다 (팀 승인 전). 전문 검색으로 바꾸는 것은 팀 회의 결정 후.
SEARCH_TERMS = ["홍역", "의심환자", "학교", "담당교사", "의료기관", "보건소", "신고", "보고", "진료", "안내", "관리인"]


def search_blocks(request: QueryInput, k: int = 5) -> list[dict]:
    """허용 문서 안에서 질문과 관련 있는 블록을 점수 순으로 k개 돌려준다 (로컬 블록 파일을 읽는다)."""
    allowed = set(request["allowed_document_ids"])
    scored = []
    for line in BLOCKS_FILE.read_text(encoding="utf-8").splitlines():
        b = json.loads(line)
        if b["document_id"] not in allowed:
            continue
        hay = (b["text"] or "") + " " + (b.get("section") or "")
        score = sum(hay.count(term) for term in SEARCH_TERMS)
        if score > 0:
            scored.append((score, b))
    scored.sort(key=lambda item: item[0], reverse=True)  # 점수가 같으면 파일 순서를 그대로 둔다
    return [b for _, b in scored[:k]]


def expected_chunk_id(card: dict) -> str:
    """카드가 가리키는 블록의 전체 ID (문서 ID + 쪽 + 블록 번호)."""
    doc_id = DOCS[card["출처"][0]][0]
    return f"{doc_id}:p{card['출처'][1]:04d}:b{card['블록']:02d}"


def build_citations(cards: list[dict]) -> list[dict]:
    """카드의 출처를 Citation 목록으로 바꾼다.

    설명
    - Citation 은 chunk_id(블록 단위)로 구분된다. 같은 chunk_id 는 한 번만 넣는다.
      (contract_local 의 검사가 중복 chunk_id 를 실패로 본다.)
    - make_citation 은 chunk_id 를 만들고, 구절이 쪽 안에 있는지 확인한다.
      build_citations 는 여기에 더해 구절이 그 블록 안에 있는지 확인한다.
    - 블록 번호는 카드의 "블록" 칸에서 읽는다. 그래야 카드마다 맞는 블록을 가리킨다.
    """
    citations: list[dict] = []
    seen_chunk_ids: set[str] = set()

    for card in cards:
        key, page, quote = card["출처"]  # 튜플을 세 변수로 나눠 받는다.
        block = card["블록"]             # 카드가 가리키는 블록 번호

        citation = make_citation(key, page, quote, block=block)
        # make_citation 은 쪽 안만 확인하므로, 구절이 이 블록 원문 안에 있는지 따로 확인한다.
        if not _in_block(citation["chunk_id"], quote):
            raise ValueError(f"구절이 {citation['chunk_id']} 블록 안에 없습니다: {quote}")

        # 같은 chunk_id 가 이미 있으면 건너뛴다. (두 카드가 같은 블록을 가리킬 수 있다.)
        if citation["chunk_id"] in seen_chunk_ids:
            continue
        seen_chunk_ids.add(citation["chunk_id"])
        citations.append(citation)

    return citations


def build_findings(cards: list[dict], citations: list[dict]) -> list[dict]:
    """카드마다 Finding(주장) 하나를 만든다.

    설명: Finding 은 "무엇을 주장하는가"(claim)와 "어떤 근거를 쓰는가"(chunk_ids)를 적는다.
    - claim: 한 문장 주장. 예) "학교 담당교사는 교내 홍역(의심)환자 발생 시 진료를 안내한다."
    - chunk_ids: 이 주장이 쓴 Citation 의 chunk_id 목록. citations 에 있는 것만 써야 한다.
    - action_ids, disease_ids, concept_ids: 지금은 빈 목록으로 둬도 된다 (그래프 연결은 나중 단계).
    """
    findings: list[dict] = []

    for card in cards:
        claim = f"{card['주체']}는 {card['조건']} {card['행동']}한다."  

        # 이 카드가 가리키는 블록의 전체 ID (문서 ID + 쪽 + 블록 번호)를 만든다.
        doc_id = DOCS[card["출처"][0]][0]
        expected_chunk_id = f"{doc_id}:p{card['출처'][1]:04d}:b{card['블록']:02d}"
        # 같은 쪽의 다른 블록이 섞이지 않도록, 전체 ID가 같은 Citation만 고른다.
        chunk_ids: list[str] = [c["chunk_id"] for c in citations if c["chunk_id"] == expected_chunk_id]


        findings.append({
            "claim": claim,
            "chunk_ids": chunk_ids,
            "action_ids": [],
            "disease_ids": [],
            "concept_ids": [],
        })

    return findings


# ---------------------------------------------------------------------------
# 3) 진입점
# ---------------------------------------------------------------------------

def run(request: QueryInput) -> QueryOutput:
    """질문 하나를 받아 샘플 결과를 QueryOutput 형식으로 돌려준다 (11/4 T3 전 검증용)."""

    # 샘플이 아직 비어 있으면 실행을 멈추고 이유를 알려준다.
    if not SAMPLE_CARDS:
        raise NotImplementedError("SAMPLE_CARDS 가 비어 있습니다. Part A 카드를 먼저 채우세요.")

    # 1) 질문 확인과 허용 문서 확인.
    #    - 질문이 샘플과 다르면 근거가 없다고 답한다.
    #    - 샘플 근거 문서(DOCS 의 문서 ID)가 허용 목록에 없으면 근거를 쓸 수 없으니 같은 방식으로 답한다.
    sample_doc_ids = {DOCS[card["출처"][0]][0] for card in SAMPLE_CARDS}
    if not is_sample_question(request) or not sample_doc_ids <= set(request["allowed_document_ids"]):
        result: QueryOutput = {
            "domain": "prevention_legal",
            "status": "INSUFFICIENT_EVIDENCE",   # 근거가 부족할 때 쓰는 상태
            "findings": [],
            "citations": [],
            "action_ids": [],
            "warnings": ["이 질문은 고정 샘플 범위 밖이거나, 샘플 근거 문서가 허용 목록에 없습니다."],
            "answer": "",
        }
        validate_query_output(result, request)
        return result

    # 2) 검색: 질문과 관련 있는 블록을 찾는다 (허용 문서 안에서만)
    retrieved_ids = {b["chunk_id"] for b in search_blocks(request, k=5)}

    # 3) 샘플 카드 중 검색 결과 상위 5개에 드는 블록을 가리키는 카드만 근거로 쓴다
    usable_cards = [c for c in SAMPLE_CARDS if expected_chunk_id(c) in retrieved_ids]
    if not usable_cards:
        result: QueryOutput = {
            "domain": "prevention_legal",
            "status": "INSUFFICIENT_EVIDENCE",
            "findings": [],
            "citations": [],
            "action_ids": [],
            "warnings": ["검색 상위 결과에 샘플 근거 블록이 없습니다."],
            "answer": "",
        }
        validate_query_output(result, request)
        return result

    # 4) 근거와 주장 만들기 (검색에서 살아남은 카드만)
    citations = build_citations(usable_cards)
    findings = build_findings(usable_cards, citations)

    # 4) 결과 조립. 각 키의 뜻은 아래와 같다.
    #    - domain: 이 결과를 만든 도메인 (여기서는 "prevention_legal")
    #    - status: ANSWERED / PARTIAL / INSUFFICIENT_EVIDENCE / ERROR 중 하나
    #    - warnings: 사람이 봐야 할 주의 문구 (예: 신고 주체를 단정할 수 없음)
    #    - answer: 사용자에게 보여 줄 짧은 답
    result = {
        "domain": "prevention_legal",
        "status": "PARTIAL",  # 근거가 일부만 있으므로 PARTIAL
        "findings": findings,
        "citations": citations,
        "action_ids": [],       # 그래프 연결 전까지는 빈 목록
        "warnings": [
    "의료기관의 행동은 이 샘플에 포함되지 않았습니다.",
    "법률(감염병예방법)의 조건은 이 샘플에 포함되지 않았습니다.",
],

        "answer": "학교 담당교사는 교내 홍역(의심)환자가 발생하면 의료기관 진료를 안내하고 관할 보건소에 신고합니다.",
        
    }

    # 5) 최종 검사. 키 누락, 허용 문서 밖 인용, 빈 발췌문 등이 있으면 ValueError 가 난다.
    validate_query_output(result, request)
    return result
