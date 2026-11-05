"""Neo4j 대조 검증(팀 지시서 4-4·B10) - school 임시본. 공통 validation.py가 생기면 교체한다.

구조 검사(validate_query_output)는 없는 chunk_id를 알 수 없다(T6). 여기서 실제 DB를 읽어 확인한다.
- Citation: chunk 존재, 그 청크가 Citation.document_id 문서의 HAS_CHUNK인지, pdf_page·printed_page 일치,
  excerpt가 청크 원문에 있는지(표시 서식 기호만 무시, 낱말은 그대로 비교).
- Finding: disease_ids·concept_ids가 사전 노드로 존재하는지, action_ids가 존재하고 Finding이 인용한
  청크 중 하나로 SUPPORTED_BY가 있는지.
검증을 통과해도 주장·담당·강도가 원문과 맞는다는 뜻은 아니다(사람·골든셋 검수 몫).
"""

from __future__ import annotations

import re

# 비교에서 무시하는 것은 표시 서식뿐이다: Markdown 강조(*), 각주 표시([*], [1]), <br>, 공백.
# 낱말·숫자·문장부호는 그대로 비교하므로 요약·의역 발췌는 여전히 실패한다.
FORMAT_MARKS = re.compile(r"<br\s*/?>|\[\*+\]|\[\d{1,2}\]|\*+|\s+")


def excerpt_in_text(excerpt: str, text: str) -> bool:
    """발췌가 청크 원문에 있는가. 그대로 있으면 통과, 없으면 서식 기호를 뺀 뒤 다시 비교한다
    (11/05 M3: legal_search가 '**홍역(의심)환자** **[*]** **발생 시…**'의 서식만 빼고 발췌)."""
    if excerpt in text:
        return True
    plain = FORMAT_MARKS.sub("", excerpt)
    return bool(plain) and plain in FORMAT_MARKS.sub("", text)


def _rows(driver, query, database=None, **params):
    records, _, _ = driver.execute_query(query, database_=database, **params)
    return [r.data() for r in records]


def verify_against_db(result: dict, driver, database=None) -> dict:
    """반환: {'bad_citations': {chunk_id: [사유]}, 'bad_findings': {index: [사유]}}.

    DB 조회 자체가 실패하면 예외를 그대로 올린다(호출하는 쪽이 ERROR로 처리 - 대조 못 한 Finding은 쓰지 않음).
    """
    citations = {c["chunk_id"]: c for c in result["citations"]}
    found = {r["chunk_id"]: r for r in _rows(driver, """
MATCH (c:Chunk) WHERE c.chunk_id IN $ids
OPTIONAL MATCH (d:Document)-[:HAS_CHUNK]->(c)
RETURN c.chunk_id AS chunk_id, c.text AS text, c.pdf_page AS pdf_page,
       c.printed_page AS printed_page, collect(d.document_id) AS documents""", database,
                                             ids=list(citations))}
    bad_citations: dict[str, list[str]] = {}
    for chunk_id, citation in citations.items():
        row = found.get(chunk_id)
        problems = []
        if row is None:
            problems.append("DB에 없는 chunk_id")
        else:
            if row["documents"] != [citation["document_id"]]:
                problems.append(f"HAS_CHUNK 문서 불일치: {row['documents']}")
            if row["pdf_page"] != citation["pdf_page"] or row["printed_page"] != citation["printed_page"]:
                problems.append("쪽수 불일치")
            if not excerpt_in_text(citation["excerpt"], row["text"] or ""):
                problems.append("excerpt가 청크 원문에 없음")
        if problems:
            bad_citations[chunk_id] = problems

    action_ids = sorted({a for f in result["findings"] for a in f["action_ids"]})
    supports: dict[str, set[str]] = {}
    for row in _rows(driver, """
MATCH (a:SchoolAction) WHERE a.action_id IN $ids
OPTIONAL MATCH (a)-[:SUPPORTED_BY]->(c:Chunk)
RETURN a.action_id AS action_id, collect(c.chunk_id) AS chunks""", database, ids=action_ids):
        supports[row["action_id"]] = set(row["chunks"])
    concept_ids = sorted({c for f in result["findings"] for c in f["concept_ids"]})
    disease_ids = sorted({d for f in result["findings"] for d in f["disease_ids"]})
    known = _rows(driver, """
RETURN COLLECT { MATCH (k:ActionConcept) WHERE k.concept_id IN $concepts RETURN k.concept_id } AS concepts,
       COLLECT { MATCH (x:Disease) WHERE x.disease_id IN $diseases RETURN x.disease_id } AS diseases""",
                  database, concepts=concept_ids, diseases=disease_ids)[0]

    bad_findings: dict[int, list[str]] = {}
    for index, finding in enumerate(result["findings"]):
        problems = [f"인용 청크 문제: {cid}" for cid in finding["chunk_ids"] if cid in bad_citations]
        for action_id in finding["action_ids"]:
            if action_id not in supports:
                problems.append(f"DB에 없는 action_id: {action_id}")
            elif not supports[action_id] & set(finding["chunk_ids"]):
                problems.append(f"행동의 SUPPORTED_BY가 인용 청크와 연결되지 않음: {action_id}")
        problems += [f"사전에 없는 concept_id: {c}" for c in finding["concept_ids"]
                     if c not in known["concepts"]]
        problems += [f"사전에 없는 disease_id: {d}" for d in finding["disease_ids"]
                     if d not in known["diseases"]]
        if problems:
            bad_findings[index] = problems
    return {"bad_citations": bad_citations, "bad_findings": bad_findings}
