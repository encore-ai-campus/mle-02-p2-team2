"""확인 항목 조회(11/07 답변 품질 P0-2) — 질문에 답하려면 꼭 확인해야 하는 '판단 기준' 원문을 DB에서 직접 찾는다.

배경(답변품질_개선_제안서_20261107.md): "코로나 5명, 교장이 할 일"에 답하려면 학교장 신고 대상 목록·질병 급수·유행의심 기준이,
"홍역, 담임이 신고해야? 안 하면?"에 답하려면 신고의무자 조문·벌칙이 필요하다. 이 원문은 DB에 있었지만 사용자가 묻지 않은
항목이라 Tool 검색어가 되지 않았고, 답변이 조건("신고가 필요한 법정감염병인 경우")을 판단하지 못한 채 옮기기만 했다.

원칙
- 라우터가 고른 확인 항목(checks)마다 고정된 원문 패턴으로 찾는다. 읽기 전용, 허용 문서 안에서만.
- 결과는 Finding(원문 인용)·Citation으로 낸다. 발췌는 원문 그대로의 구간이다(요약하지 않는다).
- 코드가 판정하는 것은 글자 대조뿐이다: 질병 이름이 신고 대상 목록 원문에 있는지, 질병 이름 가까이에 '제N급'이 적혀 있는지.
  이 대조 결과는 facts(근거 청크와 함께)로 답변 작성기에 넘기고, 결론 문장은 작성기가 근거 번호와 함께 쓴다.
- 다른 담당자 문서(법령·질병관리청 지침)도 텍스트로 찾는다(다른 담당자 폴더를 import하지 않는다).
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

from index.school.dictionaries import compare_key

WINDOW = 520                    # 긴 청크에서 기준 낱말 앞뒤로 잘라 쓰는 발췌 길이(원문 그대로)
CHECK_LABELS = {"REPORTABLE": "신고 대상 감염병", "DISEASE_CLASS": "법정감염병 급수", "STAGE": "대응 단계·유행의심 기준",
                "REPORTER_DUTY": "신고 의무자", "PENALTY": "미신고 벌칙", "EXCLUSION": "등교중지 기간",
                "ROLE_TASKS": "역할별 단계 할 일", "ATTENDANCE": "출석 인정", "REPORT_EDU": "교육청 보고"}
# 역할 이름 → 경기도 가이드북 '단계별 주요 구성원의 주요 역할(예시)' 표의 구성원 표기와 질문 속 표현
ROLE_WORDS = {"학교장": ("교장", "학교장"), "교감": ("교감",), "교무부장": ("교무부장",), "학년부장": ("학년부장",),
              "담임교사": ("담임",), "보건교사": ("보건교사", "보건선생", "보건쌤")}
DOC_NAMES = {"moe_school_manual_v3": "교육부 매뉴얼", "gyeonggi_school_guide_2025": "경기도 가이드북",
             "moleg_infectious_disease_act_21521": "감염병예방법", "kdca_covid19_2026": "질병관리청 코로나19 관리지침",
             "kdca_respiratory_2026": "질병관리청 호흡기감염병 관리지침", "kdca_measles_2026": "질병관리청 홍역 대응 지침",
             "kdca_diagnosis_reporting_2026": "질병관리청 진단·신고기준 지침",
             "kdca_flu_facility_2026": "질병관리청 인플루엔자 관리지침(학교용)",
             "kdca_hfmd_2026": "질병관리청 수족구병 관리지침", "kdca_vaccine_preventable_2026": "질병관리청 예방접종 대상 감염병 지침"}
# 확인 항목별 원문 패턴: (설명, Cypher WHERE 조건, 발췌 기준 낱말, 최대 개수). $docs·$names를 쓴다.
_IN_DOCS = "any(d IN $docs WHERE c.chunk_id STARTS WITH d + ':')"
PATTERNS = {
    "REPORTABLE": [
        ("학교장 신고 대상 감염병 목록",
         "c.text CONTAINS '콜레라' AND c.text CONTAINS '결핵' AND c.text CONTAINS '홍역' AND c.text CONTAINS '신고방법'",
         "종류", 1),
    ],
    "STAGE": [
        ("유행의심 기준", "c.text CONTAINS '유행의심 기준' AND c.text CONTAINS '최대 잠복기 이내에'", "유행의심 기준", 1),
        ("대응 제3단계 상황", ("c.text CONTAINS '대응 제3단계' AND c.text CONTAINS '상황' AND c.text CONTAINS '2명 이상'"
                           " AND size(c.text) < 400"), "대응 제3단계", 1),
    ],
    "REPORT_EDU": [
        ("교육청 보고(대응 제2단계 주요 활동)", "c.text CONTAINS '나이스(NEIS)를 통해 교육(지원)청에 보고'", "주요 활동", 1),
    ],
    "ATTENDANCE": [
        ("출석 인정 결석 기본 원칙", "c.text CONTAINS '출석 인정 결석 대상' AND c.text CONTAINS '등교'", "기본 원칙", 1),
        ("학업성적관리 출석 인정", "c.text CONTAINS '출석으로 인정한다' AND c.text CONTAINS '감염병'", "출석으로 인정", 1),
    ],
    "REPORTER_DUTY": [
        ("법 제12조① 그 밖의 신고의무자", "c.text CONTAINS '제12조(그 밖의 신고의무자)' AND c.chunk_id STARTS WITH 'moleg_'",
         "제12조", 1),
        ("법 제12조② 신고의무자가 아닌 사람", "c.text CONTAINS '신고의무자가 아니더라도' AND c.chunk_id STARTS WITH 'moleg_'",
         "신고의무자", 1),
    ],
    "PENALTY": [
        ("미신고 벌칙", "c.text CONTAINS '벌금' AND c.text CONTAINS '제12조제1항'", "미신고시 벌칙", 1, 760),
        ("미신고 벌칙(조문)", "c.text CONTAINS '벌칙' AND c.text CONTAINS '신고' AND c.chunk_id STARTS WITH 'moleg_'",
         "벌칙", 2),
    ],
}
DISEASE_PATTERNS = {
    "DISEASE_CLASS": ("질병 급수",
                      "any(n IN $names WHERE c.text CONTAINS n) AND c.text =~ '(?s).*제\\s*[1-4]\\s*급.*'", 1),
    "EXCLUSION": ("질병별 등교중지 기간",
                  ("any(n IN $names WHERE c.text CONTAINS n) AND (c.text CONTAINS '등교중지' OR c.text CONTAINS '등교 중지')"
                   " AND (c.chunk_id STARTS WITH 'moe_' OR c.chunk_id STARTS WITH 'gyeonggi_' OR"
                   " c.chunk_id STARTS WITH 'kdca_flu_facility')"), 3),
}
CLASS_RE = re.compile(r"제\s*([1-4])\s*급")
# 선별 뒤에도 항상 남기는 확인 항목(판단 규칙). 등교중지 표는 질병·학교급마다 행이 많아 근거 선별에 맡긴다.
PINNED_CHECKS = {"REPORTABLE", "DISEASE_CLASS", "STAGE", "REPORTER_DUTY", "PENALTY", "ROLE_TASKS", "ATTENDANCE",
                 "REPORT_EDU"}
PENALTY_SENTENCE_RE = re.compile(r"[｢「]?\s*감염병의\s*예방\s*및\s*관리에\s*관한\s*법률\s*[｣」]?\s*제12조제1항에\s*따른\s*신고를\s*"
                                 r"게을리한.*?벌금에\s*처한다")
CLASS_HINTS = ("지정되어", "질병분류", "목록 항목", "감염병 목록")


ACT_WORDS = ("신고", "보고", "할일", "해야", "조치", "대응", "어떻게", "교장", "담임", "보건교사", "학교는", "학교가")
SPREAD_WORDS = ("유행", "퍼지", "퍼져", "여러", "몇명", "명씩", "집단", "번지")
PENALTY_WORDS = ("처벌", "벌금", "벌칙", "과태료", "안하면", "안했을", "불이익", "어떻게돼", "어떻게되")
EXCLUSION_WORDS = ("등교", "격리", "출석", "쉬어", "며칠", "복귀")


def augment_checks(question: str, situation: dict, checks: list[str]) -> list[str]:
    """라우터가 고른 확인 항목에 질문 낱말로 분명한 항목을 더한다(LLM이 빠뜨리는 경우 보완, 11/07 데모 D01).
    지우지는 않는다."""
    key = compare_key(question)
    out = list(checks)
    disease = (situation or {}).get("disease", "").strip()
    count = (situation or {}).get("count") or 0
    if disease and any(w in key for w in ACT_WORDS):
        out += ["REPORTABLE", "DISEASE_CLASS", "REPORT_EDU"]
    status = (situation or {}).get("status", "")
    if disease and (status in ("확진", "의심") or any(w in key for w in ACT_WORDS)):
        out.append("EXCLUSION")
    if target_roles(question, situation):
        out.append("ROLE_TASKS")
    if "출석" in key or "결석" in key:
        out.append("ATTENDANCE")
    if count >= 2 or any(w in key for w in SPREAD_WORDS):
        out.append("STAGE")
    if "신고" in key or "알려야" in key:
        out.append("REPORTER_DUTY")
    if any(w in key for w in PENALTY_WORDS):
        out += ["PENALTY", "REPORTER_DUTY"]
    if disease and any(w in key for w in EXCLUSION_WORDS):
        out.append("EXCLUSION")
    return list(dict.fromkeys(out))


def target_roles(question: str, situation: dict) -> list[str]:
    """질문자 역할(situation.asker_role)과 질문에 나온 역할 → 역할 표의 구성원 이름."""
    text = compare_key(" ".join([question, (situation or {}).get("asker_role", "")]))
    return [role for role, words in ROLE_WORDS.items() if any(compare_key(w) in text for w in words)]


def target_stages(question: str, situation: dict) -> list[int]:
    """상황 → 역할 표에서 볼 대응 단계. 여러 명·확산 → 제3단계, 확진·의심 1명 → 제2단계(+진료 결과 확인이 있는 제1단계),
    증상만 → 제1단계. 유행 종료를 물으면 복구단계(0으로 표시). 판단할 사실이 없으면 빈 목록(전 단계를 보지 않는다)."""
    key = compare_key(question)
    situation = situation or {}
    if "종료" in key or "끝났" in key or "끝나" in key:
        return [0]
    if (situation.get("count") or 0) >= 2 or any(w in key for w in SPREAD_WORDS):
        return [3]
    if situation.get("status") in ("확진", "의심"):
        return [2, 1]
    if situation.get("status") == "증상" or any(w in key for w in ("열이", "발열", "증상", "아파", "구토", "설사")):
        return [1]
    return []


def disease_names(disease: str, books: dict) -> list[str]:
    """질문의 병명 → 사전 이름·별칭(같은 질병). 사전에 없으면 원래 낱말만."""
    if not disease.strip():
        return []
    key = compare_key(disease)
    names = [disease.strip()]
    for entry in books["disease"]["entries"]:
        surfaces = [entry["name"], *entry.get("aliases", [])]
        if any(len(compare_key(s)) >= 2 and (compare_key(s) in key or key in compare_key(s)) for s in surfaces):
            names += surfaces
    # 표기 흔들림: '코로나19' ↔ '코로나'(한글 뒤 19만)
    names += [n[:-2] for n in names if re.search(r"[가-힣]19$", n)]
    return [n for n in dict.fromkeys(names) if len(compare_key(n)) >= 2]


def window(text: str, anchors: list[str], size: int = WINDOW) -> str:
    """기준 낱말이 처음 나오는 곳 앞뒤로 원문을 자른다(원문 그대로, 앞뒤 공백만 정리)."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= size:
        return text
    positions = []
    for anchor in anchors:            # 기준 낱말 글자 사이 공백·줄바꿈 허용
        found = re.search(r"\s*".join(re.escape(ch) for ch in anchor.replace(" ", "")), text) if anchor else None
        if found:
            positions.append(found.start())
    start = max(0, min(positions) - size // 6) if positions else 0
    return text[start:start + size].strip()


def _rows(deps: dict, where: str, **params) -> list[dict]:
    query = (f"MATCH (c:Chunk) WHERE {_IN_DOCS} AND ({where}) "
             "RETURN c.chunk_id AS chunk_id, c.text AS text, c.pdf_page AS pdf_page, "
             "c.printed_page AS printed_page, c.section AS section ORDER BY size(c.text) ASC LIMIT 12")
    records, _, _ = deps["driver"].execute_query(query, database_=deps.get("database"), routing_="r", **params)
    return [r.data() for r in records]


def _doc(chunk_id: str) -> str:
    return chunk_id.split(":", 1)[0]


def gather_checks(checks: list[str], situation: dict, allowed: list[str], deps: dict) -> dict:
    """{'findings', 'citations', 'facts', 'found': {check: [chunk_id]}, 'seconds'}. 실패하면 예외(호출자가 건너뛴다)."""
    started = time.perf_counter()
    books = deps["books"]
    names = disease_names(situation.get("disease", ""), books)
    params = {"docs": list(allowed), "names": names}
    picked: dict[str, dict] = {}          # chunk_id → {row, check, label, anchors}
    found: dict[str, list[str]] = {}
    roles = target_roles(situation.get("_question", ""), situation)
    stages = target_stages(situation.get("_question", ""), situation)
    jobs = []                               # (check, label, where, anchor, limit, size)
    for check in checks:
        specs = list(PATTERNS.get(check, []))
        if check == "ROLE_TASKS":
            for role in roles:
                for stage in stages:
                    stage_word = "복구단계" if stage == 0 else f"제{stage}단계"
                    where = ("c.chunk_id STARTS WITH 'gyeonggi_school_guide_2025:p01' AND c.text CONTAINS '주요 역할' "
                             f"AND c.text =~ '(?s).*\\|\\s*{role}\\s*\\|[^|]*{stage_word}.*'")
                    specs.append((f"역할 예시: {role} {'복구단계' if stage == 0 else f'대응 {stage_word}'}", where,
                                  role, 1, 900))
        if check in DISEASE_PATTERNS and names:
            label, where, limit = DISEASE_PATTERNS[check]
            specs.append((label, where, names[0], limit))
        for label, where, anchor, limit, *rest in specs:
            jobs.append((check, label, where, anchor, limit, rest[0] if rest else WINDOW))
    with ThreadPoolExecutor(max_workers=8) as pool:      # 항목별 질의를 동시에(순차 1~2.5초 → 가장 느린 질의 하나)
        results = list(pool.map(lambda job: _rows(deps, job[2], **params), jobs))
    for (check, label, _where, anchor, limit, size), rows in zip(jobs, results, strict=True):
        if check == "DISEASE_CLASS":   # 급수를 '지정'·'질병분류'·목록으로 적은 청크, 병명과 '제N급'이 가까운 청크를 앞에
            rows.sort(key=lambda r: (not any(k in r["text"] for k in CLASS_HINTS), _class_distance(r["text"], names)))
        for row in rows[:limit]:
            anchors = [anchor, *names] if check in DISEASE_PATTERNS else [anchor]
            picked.setdefault(row["chunk_id"], {"row": row, "check": check, "label": label, "anchors": anchors,
                                                "size": size})
            if row["chunk_id"] not in found.setdefault(check, []):
                found[check].append(row["chunk_id"])
    findings, citations, facts = [], [], []
    for chunk_id, item in picked.items():
        row = item["row"]
        excerpt = window(row["text"], item["anchors"], item["size"])
        doc = _doc(chunk_id)
        citations.append({"document_id": doc, "chunk_id": chunk_id, "pdf_page": row["pdf_page"],
                          "printed_page": row.get("printed_page"), "excerpt": excerpt})
        findings.append({"claim": f"[{DOC_NAMES.get(doc, doc)} PDF {row['pdf_page']}쪽 · 판단 기준: {item['label']}] "
                                  f"원문: “{excerpt}”",
                         "chunk_ids": [chunk_id], "action_ids": [], "disease_ids": [], "concept_ids": []})
    disease = situation.get("disease", "").strip()
    if disease and names:
        for chunk_id in found.get("REPORTABLE", []):
            text = picked[chunk_id]["row"]["text"]
            hit = next((n for n in names if n in text), None)
            facts.append({"chunk_id": chunk_id, "text": (f"학교장 신고 대상 감염병 목록 원문에 '{hit}'이(가) 있다." if hit else
                                                         f"학교장 신고 대상 감염병 목록 원문에 '{disease}'이(가) 없다.")})
        for chunk_id in found.get("DISEASE_CLASS", []):
            grade = _class_near(picked[chunk_id]["row"]["text"], names)
            if grade:
                facts.append({"chunk_id": chunk_id, "text": f"원문에 '{disease}'이(가) 제{grade}급감염병으로 적혀 있다."})
                break
    for chunk_id in found.get("REPORTABLE", [])[:1]:
        text = re.sub(r"\s+", " ", picked[chunk_id]["row"]["text"])
        if "제12조 및" in text and "시행규칙 제8조" in text:
            facts.append({"chunk_id": chunk_id, "text": "이 신고 대상 목록의 법적 근거는 '감염병예방법 제12조 및 시행규칙 제8조'로 "
                                                       "적혀 있다(목록에 없는 감염병은 이 목록에 따른 학교장 신고 대상이 아니다)."})
    for chunk_id in found.get("PENALTY", []):
        text = re.sub(r"\s+", " ", picked[chunk_id]["row"]["text"])
        sentence = PENALTY_SENTENCE_RE.search(text)
        if sentence:
            facts.append({"chunk_id": chunk_id, "text": f"벌칙 원문: “{sentence.group(0).strip(' ,')}.”"})
    for chunk_id in found.get("REPORTER_DUTY", []):
        text = re.sub(r"\s+", " ", picked[chunk_id]["row"]["text"])
        if "학교" in text and "관리인, 경영자 또는 대표자" in text:
            facts.append({"chunk_id": chunk_id, "text": "법 제12조①은 학교 등 여러 사람이 모이는 장소의 '관리인, 경영자 또는 대표자'를 "
                                                       "신고의무자로 적고 있다(담임교사는 이 목록에 적혀 있지 않다)."})
        elif "신고의무자가 아니더라도" in text:
            facts.append({"chunk_id": chunk_id, "text": "법 제12조②는 신고의무자가 아니더라도 감염병 의심자를 발견하면 "
                                                       "보건소장에게 알려야 한다고 적고 있다."})
    facts = list({f["text"]: f for f in facts}.values())          # 같은 사실(두 문서의 같은 목록)은 한 번만
    pinned = [c for check, ids in found.items() if check in PINNED_CHECKS for c in ids]
    return {"findings": findings, "citations": citations, "facts": facts, "found": found, "pinned": list(dict.fromkeys(pinned)),
            "names": names, "seconds": round(time.perf_counter() - started, 2)}


def _class_distance(text: str, names: list[str]) -> int:
    positions = [m.start() for n in names for m in re.finditer(re.escape(n), text)]
    grades = [m.start() for m in CLASS_RE.finditer(text)]
    if not positions or not grades:
        return 10**6
    return min(abs(p - g) for p in positions for g in grades)


def _class_near(text: str, names: list[str], reach: int = 160) -> str | None:
    """병명에서 reach 글자 안의 '제N급' 중 가장 가까운 급수."""
    best = None
    for name in names:
        for m in re.finditer(re.escape(name), text):
            for g in CLASS_RE.finditer(text):
                gap = abs(g.start() - m.start())
                if gap <= reach and (best is None or gap < best[0]):
                    best = (gap, g.group(1))
    return best[1] if best else None
