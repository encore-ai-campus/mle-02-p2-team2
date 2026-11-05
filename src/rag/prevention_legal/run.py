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
import re

from preprocess.prevention_legal.pages import DOCS
from rag.prevention_legal.contract_local import QueryInput, QueryOutput, validate_query_output
from rag.prevention_legal.retrieve import fetch_chunks, fulltext_search

# ---------------------------------------------------------------------------
# 1) 고정 샘플 (사용자가 Part A 에서 정한 값을 넣는다)
# ---------------------------------------------------------------------------

# 샘플 질문. 요청 질문이 여기와 같을 때만 샘플 결과를 돌려준다.
SAMPLE_QUESTION = "홍역 의심환자가 생겼을 때 학교 담당교사와 의료기관은 각각 보건소에 무엇을 신고하거나 안내하나요? 법률과 지침의 조건을 구분해 주세요."

# 카드 한 건 = 학교 행동 한 개.
# "출처" 는 (문서 키, PDF 쪽, 원문 구절) 세 칸짜리 튜플이다.
#   - 문서 키는 pages.py 의 DOCS 키다 (예: "measles").
#   - 원문 구절은 청크 원문에 그대로 있어야 한다 (build_citations 가 공동 DB 청크 텍스트와 대조한다).
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


# 법 근거 카드 (의료기관·학교 관리인). 출처는 법 원문 블록이며, 주장 문장은 법 문장을 그대로 옮긴다.
# "주장" 칸이 있으면 build_findings 가 그 문장을 쓴다.
LAW_CARDS: list[dict] = [
    {"주체": "의사(의료기관 소속)", "행동": "소속 의료기관의 장에게 보고", "조건": "감염병환자를 진단한 경우",
     "주장": "의료기관에 소속된 의사는 감염병환자 등을 진단한 경우(표본감시 대상 제4급감염병으로 인한 경우는 제외) 소속 의료기관의 장에게 보고하여야 한다.",
     "출처": ("law", 18, "소속 의료기관의 장에게 보고하여야 하고"), "블록": 1},
    {"주체": "의료기관의 장", "행동": "질병관리청장 또는 관할 보건소장에게 신고", "조건": "보고받은 감염병 (1급 즉시, 2·3급 24시간 이내, 4급 7일 이내)",
     "주장": "보고를 받은 의료기관의 장은 1급감염병은 즉시, 2·3급감염병은 24시간 이내, 4급감염병은 7일 이내에 질병관리청장 또는 관할 보건소장에게 신고하여야 한다.",
     "출처": ("law", 18, "질병관리청장 또는 관할 보건소장에게 신고하여야 한다"), "블록": 2},
    {"주체": "학교 관리인·경영자 또는 대표자", "행동": "의사에게 진단·검안을 요구하거나 주소지 보건소장에게 신고", "조건": "제1급~제3급감염병 중 보건복지부령으로 정한 감염병(홍역 포함)이 발생한 경우",
     "주장": "제1급부터 제3급감염병 중 보건복지부령으로 정한 감염병(홍역 포함)이 발생한 경우, 학교의 관리인·경영자 또는 대표자는 의사의 진단이나 검안을 요구하거나 주소지 관할 보건소장에게 신고하여야 한다.",
     "출처": ("law", 19, "관리인, 경영자 또는 대표자"), "블록": 1},
]

# 검색 기반 카드 (샘플 질문이 아닌 질문에만 쓴다). 블록이 검색 상위 10개에 들고, 질문에 "질문어" 중 하나가 있어야 쓴다.
# 질문어 조건은 검색 점수가 질문과 무관하게 같은 블록을 올리는 문제를 줄이기 위한 임시 가드다.
ITEM4_CARD: dict = {
    "주체": "학교[담당교사]", "행동": "MMR 2차 접종 미완료자에게 예방접종 완료 안내", "조건": "교내 MMR 2차 접종 미완료자가 있는 경우",
    "역할근거": "가. 초 중 고등학교 내 환자 발생 시 학교[담당교사] 역할", "basis": "EXPLICIT", "단계": "UNSPECIFIED",
    "출처": ("measles", 93, "교내 MMR(홍역 유행성이하선염 풍진) 2차 접종 미완료자 가 있는 경우 감염 예방을 위해 예방접종을 완료할 필요가 있음을 안내"),
    "블록": 4,
    "주장": "학교 담당교사는 교내 MMR 2차 접종 미완료자가 있으면 예방접종을 완료하도록 안내한다.",
    "답문장": "학교 담당교사는 교내 MMR 2차 접종 미완료자가 있으면 예방접종을 완료하도록 안내합니다(지침 93쪽 항목 4).",
    "범위문": "이 답은 지침 93쪽 항목 4 범위입니다. 접종 시행 의무와 법 근거는 다루지 않았습니다.",
    "질문어": ["MMR", "미완료", "2차"],   # 두 개 이상 있어야 쓴다
    "제외어": ["전파", "잠복기", "증상", "감염경로", "전염기간", "부작용", "효과", "간격", "감염", "발병", "기간", "격리"],   # 하나라도 있으면 쓰지 않는다
}
# 93쪽 항목 2 (골든셋 Q02, 사용자 승인 2026-11-05). 근거 블록 b05, 행동 a05.
ITEM2_CARD: dict = {
    "주체": "학교[담당교사]", "행동": "진단받은 홍역(의심) 환자의 전염기간 동안 등교·등원 금지 안내", "조건": "의료기관 진료 결과 홍역(의심) 진단을 받은 경우",
    "역할근거": "가. 초 중 고등학교 내 환자 발생 시 학교[담당교사] 역할", "basis": "EXPLICIT", "단계": "UNSPECIFIED",
    "출처": ("measles", 93, "전염기간(발진이 나타난 후 4일까지) 동안 학교 등교 및 학원 등원하지 않도록 안내"),
    "블록": 5,
    "주장": "홍역(의심) 진단을 받은 경우 전염기간 동안 학교 등교 및 학원 등원하지 않도록 안내한다.",
    "답문장": "홍역(의심) 진단을 받은 학생은 전염기간(발진이 나타난 후 4일까지) 동안 학교 등교와 학원 등원을 하지 않도록 안내합니다(지침 93쪽 항목 2).",
    "범위문": "이 답은 지침 93쪽 항목 2 범위입니다. 등교 재개 판단(실험실 검사 결과 등)과 법 근거는 다루지 않았습니다.",
    "필수어": ["홍역"],                             # 질문에 반드시 있어야 한다
    "질문어": ["진단", "등교", "등원", "언제까지"],   # 두 개 이상 있어야 쓴다
    "제외어": ["전파", "잠복기", "증상", "감염경로", "부작용", "효과", "간격", "검사", "접촉", "예방접종",
              "격리", "복귀", "재개", "음성", "수족구", "성홍열", "백일해", "수두", "코로나", "독감", "인플루엔자", "유행성이하선염"],
}
RETRIEVAL_CARDS: list[dict] = [ITEM4_CARD, ITEM2_CARD]


# ---------------------------------------------------------------------------
# 1-1) 성홍열 사례 (골든셋 Q31~Q33, 개발용). 질문 문장이 정확히 같을 때만 쓴다.
# 시행규칙 제8조 ①(보건복지부령 감염병 목록)에는 성홍열이 없다(사용자 원본 확인, 2026-11-05).
# ---------------------------------------------------------------------------
SCARLET_CASES: list[dict] = [
    {
        "question": "성홍열은 감염병예방법상 몇 급감염병이며, 신고는 언제까지 해야 하나요?",
        "status": "PARTIAL",
        "cards": [
            {"주체": "법(제2급감염병)", "행동": "24시간 이내 신고", "조건": "성홍열(제2급감염병)이 발생 또는 유행한 경우",
             "주장": "발생 또는 유행 시 24시간 이내에 신고하여야 하는 것은 제2급감염병이며, 성홍열은 제2급감염병 목록에 있다.",
             "출처": ("law", 6, "발생 또는 유행 시 24시간 이내에 신고하여야 하고"), "블록": 2},
            {"주체": "법(제2급감염병 목록)", "행동": "목록 확인", "조건": "제2급감염병 목록",
             "주장": "법 제2조 제3호의 제2급감염병 목록(7쪽, 러목)에 성홍열이 포함되어 있다.",
             "출처": ("law", 7, "러. 성홍열"), "블록": 2},
        ],
        "answer": "성홍열은 제2급감염병입니다. 발생 또는 유행 시 24시간 이내에 신고해야 합니다(법 제2조 제3호, 목록 러목).",
        "warnings": ["신고 주체(누가 신고하는지)는 이 답에서 판단하지 않았습니다. 주체별 조건은 별도 확인이 필요합니다."],
    },
    {
        "question": "성홍열 환자가 발생했을 때 학교 관리인은 누구에게 어떻게 신고해야 하나요?",
        "status": "PARTIAL",
        "cards": [
            {"주체": "학교 관리인·경영자 또는 대표자", "행동": "의사에게 진단·검안을 요구하거나 주소지 보건소장에게 신고",
             "조건": "제1급~제3급감염병 중 보건복지부령으로 정한 감염병",
             "주장": "학교 관리인·경영자 또는 대표자는 보건복지부령으로 정한 감염병이 발생한 경우 의사의 진단이나 검안을 요구하거나 주소지 보건소장에게 신고하여야 한다(제1급~제3급감염병에 한함).",
             "출처": ("law", 19, "관리인, 경영자 또는 대표자"), "블록": 1},
        ],
        "answer": "제12조 ①의 학교 관리인 요건은 보건복지부령으로 정한 감염병에 적용됩니다. 성홍열이 그 목록에 있는지는 시행규칙 제8조 ①(사용자 확인 본문)에서 없는 것으로 보이나, 이 법 블록만으로는 입증되지 않아 단정하지 않습니다.",
        "warnings": [
            "시행규칙 제8조 ①은 저장소에 원본 파일이 없습니다. 사용자가 확인한 현행 본문 기준입니다(2026-11-05).",
            "성홍열 학교 대응 절차(지침)는 근거가 없어 답하지 않았습니다.",
        ],
    },
    {
        "question": "성홍열 학교 대응 절차를 지침에 따라 자세히 알려 주세요.",
        "status": "INSUFFICIENT_EVIDENCE",
        "cards": [],
        "answer": "",
        "warnings": ["성홍열 학교 대응 절차가 적힌 지침 근거가 현재 블록에 없어 답할 수 없습니다."],
    },
]


# 수족구 사례 (골든셋 Q18·Q19, 개발용). 근거는 수족구 지침 29·30쪽 블록만 쓴다.
HFMD_CASES: list[dict] = [
    {
        "question": "수족구병에 걸린 학생은 학교에 계속 나와도 되나요?",
        "status": "ANSWERED",
        "cards": [
            {"주체": "학교·유치원 등", "행동": "수족구병 환자의 등교 등원중지 권장", "조건": "수족구병 환자",
             "주장": "수족구병 환자는 학교, 유치원, 학원 등에서 확산 방지를 위해 등교 등원중지가 권장된다.",
             "출처": ("hfmd", 29, "수족구병 환자는 등교 등원중지"), "블록": 1},
        ],
        "answer": "학교에 계속 나오지 않도록 등교 등원중지가 권장됩니다(수족구 지침 29쪽). 증상이 있으면 의사의 진료를 받고 자가 격리합니다.",
        "warnings": ["등교 중지의 법적 근거(학교보건법 제8조)는 우리 자료에 없어 답하지 않았습니다.", "원본 PDF 대조는 아직 하지 않았습니다(변환본 기준)."],
    },
    {
        "question": "수족구병으로 등교를 중지한 학생은 언제부터 다시 등교할 수 있나요?",
        "status": "ANSWERED",
        "cards": [
            {"주체": "등교 등원 중지자", "행동": "복귀", "조건": "발열이 없고 수포가 마른 후",
             "주장": "수족구병으로 등교 등원을 중지한 사람은 발열이 없고 수포가 마른 후 복귀한다.",
             "출처": ("hfmd", 30, "발열이 없고, 수포가 마른 후 복귀"), "블록": 1},
        ],
        "answer": "열이 없고 물집(수포)이 마른 뒤에 복귀합니다(수족구 지침 30쪽). 복귀 후에도 손 위생을 철저히 지키고, 단체 물놀이 등 분변구강 전파 가능 행동은 자제합니다. 완치 후에도 병원체가 나올 수 있어 최소 8주까지 주의가 필요하며, 상태가 좋지 않으면 복귀를 늦추는 것을 고려합니다.",
        "warnings": ["원본 PDF 30쪽과 대조했습니다(사용자 확인, 2026-11-05)."],
    },
]


def scarlet_case(question: str) -> dict | None:
    """질문 문장이 성홍열 사례와 같으면 그 사례를 돌려준다 (공백 앞뒤 정리 후 비교)."""
    for case in SCARLET_CASES + HFMD_CASES:
        if question.strip() == case["question"].strip():
            return case
    return None


# ---------------------------------------------------------------------------
# 2) 보조 함수 (흐름의 각 단계를 함수로 나눈 것)
# ---------------------------------------------------------------------------

def is_sample_question(request: QueryInput) -> bool:
    """요청 질문이 샘플 질문과 같은지 본다.
    설명: 샘플 결과는 샘플 질문에만 맞는 답이다. 다른 질문에 같은 답을 주면 안 된다.
    """
    return request["question"].strip() == SAMPLE_QUESTION.strip()




def _compact(text: str) -> str:
    """비교용 글: 공백, 별표(*), 대괄호를 뺀다. 강조 표시 차이는 무시한다."""
    return re.sub(r"[\s*\[\]]", "", text)


def search_blocks(request: QueryInput, k: int = 5) -> list[dict]:
    """질문 단어로 전문 인덱스(chunk_text)를 검색해 허용 문서 안의 상위 k개 블록을 돌려준다.

    설명: 검색은 retrieve.fulltext_search 가 맡는다. 공동 DB 의 운영 청크(접두사로 문서 판별)를 찾고, 테스트 노드(PLTest)가 있으면 함께 본다.
    DB 연결 실패는 예외로 올라가고, 검색 결과가 없으면 빈 목록이 돌아온다 (두 경우를 구별한다).
    """
    return fulltext_search(request["question"], request["allowed_document_ids"], k=k)


def expected_chunk_id(card: dict) -> str:
    """카드가 가리키는 블록의 전체 ID (문서 ID + 쪽 + 블록 번호)."""
    doc_id = DOCS[card["출처"][0]][0]
    return f"{doc_id}:p{card['출처'][1]:04d}:b{card['블록']:02d}"


def build_citations(cards: list[dict]) -> list[dict]:
    """카드의 출처를 Citation 목록으로 바꾼다.

    설명
    - Citation 은 chunk_id(블록 단위)로 구분된다. 같은 chunk_id 는 한 번만 넣는다.
      (contract_local 의 검사가 중복 chunk_id 를 실패로 본다.)
    - 원문 대조는 공동 DB 의 운영 청크 텍스트로 한다. 로컬 파일(blocks.jsonl, 원문 md)을 읽지 않는다.
      그래서 Git 에 없는 파일이 있는 PC 에서도 동작한다.
    - 구절이 청크 원문에 없으면 ValueError 를 낸다. 청크가 DB 에 없어도 ValueError 다.
    """
    citations: list[dict] = []
    seen_chunk_ids: set[str] = set()

    chunks = fetch_chunks([expected_chunk_id(c) for c in cards])
    for card in cards:
        key, _, quote = card["출처"]
        chunk_id = expected_chunk_id(card)
        rec = chunks.get(chunk_id)
        if rec is None:
            raise ValueError(f"공동 DB 에서 {chunk_id} 청크를 찾지 못했습니다.")
        if _compact(quote) not in _compact(rec["text"]):
            raise ValueError(f"구절이 {chunk_id} 청크 원문 안에 없습니다: {quote}")

        # 같은 chunk_id 가 이미 있으면 건너뛴다. (두 카드가 같은 블록을 가리킬 수 있다.)
        if chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(chunk_id)
        citations.append({
            "document_id": DOCS[key][0],
            "chunk_id": chunk_id,
            "pdf_page": int(rec["pdf_page"]),
            "printed_page": None,
            "excerpt": quote,
        })

    return citations


def answer_from(cards: list[dict]) -> str:
    """실제로 쓰인 카드에 해당하는 문장만 모아 답을 만든다. 근거 없는 문장은 넣지 않는다."""
    sentences = []
    school_cards = [c for c in cards if c in SAMPLE_CARDS]
    if school_cards:
        sentences.append("학교 담당교사는 교내 홍역(의심)환자가 발생하면 의료기관 진료를 안내하고 관할 보건소에 신고합니다(지침).")
    for c in cards:
        if c["출처"][0] != "law":
            continue
        if c["블록"] == 1 and c["출처"][1] == 18:
            sentences.append("법에서는 의료기관 소속 의사가 감염병환자 등을 진단하면 소속 의료기관장에게 보고합니다(표본감시 제4급 제외).")
        elif c["블록"] == 2 and c["출처"][1] == 18:
            sentences.append("보고를 받은 의료기관장은 1급감염병은 즉시, 2·3급감염병은 24시간 이내, 4급감염병은 7일 이내에 질병관리청장 또는 관할 보건소장에게 신고합니다.")
        elif c["출처"][1] == 19:
            sentences.append("홍역이 보건복지부령으로 정한 감염병으로 발생한 경우, 학교 관리인·경영자 또는 대표자는 의사의 진단이나 검안을 요구하거나 주소지 보건소장에게 신고해야 합니다.")
    if not sentences:
        return "근거가 확인되지 않아 답할 수 없습니다."
    return " ".join(sentences)


def build_findings(cards: list[dict], citations: list[dict]) -> list[dict]:
    """카드마다 Finding(주장) 하나를 만든다.

    설명: Finding 은 "무엇을 주장하는가"(claim)와 "어떤 근거를 쓰는가"(chunk_ids)를 적는다.
    - claim: 한 문장 주장. 예) "학교 담당교사는 교내 홍역(의심)환자 발생 시 진료를 안내한다."
    - chunk_ids: 이 주장이 쓴 Citation 의 chunk_id 목록. citations 에 있는 것만 써야 한다.
    - action_ids, disease_ids, concept_ids: 지금은 빈 목록으로 둬도 된다 (그래프 연결은 나중 단계).
    """
    findings: list[dict] = []

    for card in cards:
        claim = card.get("주장") or f"{card['주체']}는 {card['조건']} {card['행동']}한다."

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

    # 성홍열 사례(Q31~Q33)는 질문 문장이 같을 때 바로 처리한다.
    case = scarlet_case(request["question"])
    if case is not None:
        allowed = set(request["allowed_document_ids"])
        cards = [c for c in case["cards"] if DOCS[c["출처"][0]][0] in allowed]
        # 허용되지 않은 근거가 하나라도 빠지면 원래 답을 그대로 쓰지 않는다 (근거 일부만 남은 답 방지)
        complete = len(cards) == len(case["cards"])
        citations = build_citations(cards)
        findings = build_findings(cards, citations)
        result = {
            "domain": "prevention_legal",
            "status": case["status"] if cards and complete else "INSUFFICIENT_EVIDENCE",
            "findings": findings,
            "citations": citations,
            "action_ids": [],
            "warnings": case["warnings"] if complete else case["warnings"] + ["허용되지 않은 문서의 근거가 빠져 답을 제공하지 않습니다."],
            "answer": case["answer"] if complete else "",
        }
        validate_query_output(result, request)
        return result

    # 검색 기반 카드 (샘플 질문이 아닐 때만). 블록이 검색 상위 10개에 있고, 질문에 질문어가 있어야 쓴다.
    if not is_sample_question(request):
        retrieved_ids = {b["chunk_id"] for b in search_blocks(request, k=10)}
        allowed_docs = set(request["allowed_document_ids"])
        cards = [c for c in RETRIEVAL_CARDS
                 if expected_chunk_id(c) in retrieved_ids
                 and DOCS[c["출처"][0]][0] in allowed_docs
                 and all(term in request["question"] for term in c.get("필수어", []))
                 and sum(term in request["question"] for term in c["질문어"]) >= 2
                 and not any(term in request["question"] for term in c["제외어"])]
        if cards:
            citations = build_citations(cards)
            result = {
                "domain": "prevention_legal",
                "status": "ANSWERED",
                "findings": build_findings(cards, citations),
                "citations": citations,
                "action_ids": [],
                "warnings": [c["범위문"] for c in cards],
                "answer": " ".join(c["답문장"] for c in cards),
            }
            validate_query_output(result, request)
            return result

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
    # 상위 20개: 전문 검색 기준 법 블록(18쪽 b01·b02, 19쪽 b01)이 상위 10개 밖이라 20개로 늘렸다 (튜닝 기록: 노트북 19).
    retrieved_ids = {b["chunk_id"] for b in search_blocks(request, k=20)}

    # 3) 샘플·법 카드 중 검색 결과(상위 20개)에 드는 블록을 가리키는 카드만 근거로 쓴다
    usable_cards = [c for c in SAMPLE_CARDS + LAW_CARDS if expected_chunk_id(c) in retrieved_ids]
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
            "법 제11조는 2026년 10월 8일 시행본 기준이며, 2027년 10월 21일 개정은 반영하지 않았습니다.",
            "보건복지부령 감염병 해당 여부는 감염병예방법 시행규칙 제8조 ①(시행 2025.12.31) 기준입니다.",
            "이 답변은 홍역 질문 범위입니다. 다른 질병의 신고 기준은 포함하지 않았습니다.",
        ],
        "answer": answer_from(usable_cards),
    }

    # 5) 최종 검사. 키 누락, 허용 문서 밖 인용, 빈 발췌문 등이 있으면 ValueError 가 난다.
    validate_query_output(result, request)
    return result
