"""질병별 감염병 특성 표의 '등교중지(격리) 기간' 칸 → 출처별 학교 행동 적재 명세 (주승우 / school, 2026-11-08).

대상 표: 교육부 매뉴얼 [표 3-2](p49~50)·부록 감염병별 관리 방안(p221~226), 경기도 가이드북 <표 3-1>(p157~162).
- LLM을 쓰지 않는다. 표 행 청크의 머리글에서 '등교'와 '기간'이 든 칸을 찾고, 그 칸 원문을 그대로 timing_text에 둔다.
- 질병은 행의 첫 칸(감염병명)을 질병 사전 이름·별칭과 비교해 정한다(승인 질병 11개만).
- 개념은 EXCLUDE_FROM_SCHOOL. 칸이 '등교 중지 안 함'·'격리 없이' 등 등교중지를 하지 않는 내용이면 개념을 붙이지 않고
  (bundle의 omit_link 정책과 같음) 대조표에 따로 표시한다.
- 출력은 load_extra_actions.py가 읽는 명세(JSON)와 사람이 확인할 원문 대조표(md)다. DB는 읽기만 한다.

    PYTHONPATH=src uv run python -m index.school.exclusion_table_actions <spec.json> <review.md>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from index.school.dictionaries import compare_key, load_dictionaries

TABLE_PAGES = {
    "moe_school_manual_v3": [(49, 50), (221, 226)],
    "gyeonggi_school_guide_2025": [(157, 162)],
}
CONCEPT = "EXCLUDE_FROM_SCHOOL"
NO_EXCLUSION_RE = re.compile(r"등교\s*중지\s*(안\s*함|는\s*의미\s*없음)|격리\s*없이")
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
FOOTNOTE_RE = re.compile(r"(\d+\)|[⁰¹²³⁴⁵⁶⁷⁸⁹]+⁾)$")
DURATION_RE = re.compile(r"(까지|간)$")


def clean(cell: str) -> str:
    return re.sub(r"\s+", " ", BR_RE.sub(" ", cell)).strip()


def split_row(line: str) -> list[str]:
    return [clean(c) for c in line.strip().strip("|").split("|")]


def disease_index(books: dict) -> dict[str, str]:
    index = {}
    for entry in books["disease"]["entries"]:
        for name in (entry["name"], *entry.get("aliases", [])):
            if len(compare_key(name)) >= 2:
                index[compare_key(name)] = entry["disease_id"]
    return index


def parse_chunk(chunk: dict, diseases: dict[str, str]) -> dict | None:
    """표 행 청크 → 후보 1개(승인 질병 행이 아니거나 기간 칸이 없으면 None)."""
    lines = [ln for ln in chunk["text"].split("\n") if ln.strip().startswith("|")]
    if len(lines) < 2:
        return None
    header, row = split_row(lines[0]), split_row(lines[-1])
    col = next((i for i, h in enumerate(header) if "등교" in h and "기간" in h), None)
    if col is None or col >= len(row) or not row:
        return None
    disease_id = diseases.get(compare_key(row[0]))
    if not disease_id:
        return None
    return {"chunk_id": chunk["chunk_id"], "document_id": chunk["document_id"], "pdf_page": chunk["pdf_page"],
            "disease_name": row[0], "disease_id": disease_id, "header": header[col], "cell": row[col],
            "no_exclusion": bool(NO_EXCLUSION_RE.search(row[col]))}


def to_action(candidate: dict, action_id: str) -> dict:
    """기간형 칸('~까지'·'~간')만 문장 틀에 넣고, 조건부·비중지 칸은 '질병: 원문' 그대로 둔다(뜻을 바꾸지 않기 위해)."""
    name, cell = candidate["disease_name"], candidate["cell"]
    body = FOOTNOTE_RE.sub("", cell).strip()
    concept = None if candidate["no_exclusion"] else CONCEPT
    if concept and DURATION_RE.search(body):
        text = f"{name} 환자는 {body} 등교 중지(격리)한다."
    else:
        text = f"{name} 등교 중지(격리) 기간: {body}"
    return {"action_id": action_id, "action_text": text, "scope": "NAMED_DISEASES", "stage": "UNSPECIFIED",
            "modality": "UNSPECIFIED", "when_text": name, "timing_text": cell, "chunk_id": candidate["chunk_id"],
            "concept_id": concept, "disease_ids": [candidate["disease_id"]]}


def fetch(driver) -> tuple[list[dict], dict[str, list[str]], dict[str, list[str]]]:
    clauses, params = [], {}
    for i, (doc, ranges) in enumerate(TABLE_PAGES.items()):
        for j, (lo, hi) in enumerate(ranges):
            clauses.append(f"(d.document_id = $d{i} AND c.pdf_page >= $lo{i}_{j} AND c.pdf_page <= $hi{i}_{j})")
            params.update({f"d{i}": doc, f"lo{i}_{j}": lo, f"hi{i}_{j}": hi})
    with driver.session() as s:
        chunks = [dict(r) for r in s.run(f"""
MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE c.chunk_id CONTAINS ':t' AND ({' OR '.join(clauses)})
RETURN c.chunk_id AS chunk_id, d.document_id AS document_id, c.pdf_page AS pdf_page, c.text AS text
ORDER BY chunk_id""", **params)]
        existing = {}
        linked = {}
        for r in s.run("""
MATCH (a:SchoolAction)-[:SUPPORTED_BY]->(c:Chunk) WHERE c.chunk_id IN $ids
OPTIONAL MATCH (a)-[:INSTANCE_OF]->(k:ActionConcept)
RETURN c.chunk_id AS chunk_id, a.action_id AS action_id, k.concept_id AS concept""",
                       ids=[c["chunk_id"] for c in chunks]):
            linked.setdefault(r["chunk_id"], []).append(r["concept"])
        pages = sorted({f"{c['document_id']}:p{c['pdf_page']:04d}:" for c in chunks})
        for r in s.run("MATCH (a:SchoolAction) WHERE any(p IN $pages WHERE a.action_id STARTS WITH p) "
                       "RETURN a.action_id AS id", pages=pages):
            existing.setdefault(r["id"].rsplit(":", 1)[0], []).append(r["id"])
    return chunks, existing, linked


def next_id(prefix: str, used: dict[str, list[str]]) -> str:
    numbers = [int(i.rsplit(":a", 1)[1]) for i in used.get(prefix, [])]
    new = f"{prefix}:a{max(numbers, default=0) + 1:02d}"
    used.setdefault(prefix, []).append(new)
    return new


def build(chunks, existing, linked, books) -> tuple[list[dict], list[dict]]:
    diseases = disease_index(books)
    actions, skipped = [], []
    for chunk in chunks:
        candidate = parse_chunk(chunk, diseases)
        if candidate is None:
            continue
        if CONCEPT in linked.get(chunk["chunk_id"], []):
            skipped.append({**candidate, "why": "이미 등교중지 개념 행동이 있음"})
            continue
        prefix = f"{chunk['document_id']}:p{chunk['pdf_page']:04d}"
        actions.append({**to_action(candidate, next_id(prefix, existing)), "_review": candidate})
    return actions, skipped


def review_md(actions: list[dict], skipped: list[dict]) -> str:
    rows = ["| # | 문서 | PDF쪽 | 청크 | 질병 | 머리글 | 칸 원문 | 만들 행동 | 개념 |", "|---|---|---|---|---|---|---|---|---|"]
    for n, a in enumerate(actions, 1):
        r = a["_review"]
        rows.append(f"| {n} | {r['document_id']} | {r['pdf_page']} | `{r['chunk_id']}` | {r['disease_name']}({r['disease_id']}) "
                    f"| {r['header']} | {r['cell']} | {a['action_text']} | {a['concept_id'] or '없음(등교중지 안 함)'} |")
    out = ["# 질병별 등교중지 기간 표 → 학교 행동 원문 대조표 (2026-11-08)", "",
           "생성: `src/index/school/exclusion_table_actions.py`(LLM 없음, DB 읽기 전용). 칸 원문은 표 행 청크에서 그대로 가져왔다(줄바꿈 표시 `<br>`만 공백으로 바꿈).", "",
           f"- 만들 행동: {len(actions)}개", f"- 건너뜀: {len(skipped)}개(이미 등교중지 개념 행동 있음)", "", *rows]
    if skipped:
        out += ["", "## 건너뛴 행", "", *[f"- `{s['chunk_id']}` {s['disease_name']}: {s['why']}" for s in skipped]]
    return "\n".join(out) + "\n"


def main() -> None:
    from rag.prevention_legal.neo4j_conn import connect

    spec_path, review_path = Path(sys.argv[1]), Path(sys.argv[2])
    driver = connect()
    try:
        chunks, existing, linked = fetch(driver)
    finally:
        driver.close()
    actions, skipped = build(chunks, existing, linked, load_dictionaries())
    spec = {"description": "질병별 감염병 특성 표의 '등교중지(격리) 기간' 칸에서 규칙으로 만든 출처별 학교 행동(2026-11-08, "
                           "주승우 지시, Claude 생성). 칸 원문은 timing_text에 그대로 둔다.",
            "actions": [{k: v for k, v in a.items() if k != "_review"} for a in actions]}
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    review_path.write_text(review_md(actions, skipped), encoding="utf-8")
    print(f"행동 {len(actions)}개, 건너뜀 {len(skipped)}개")


if __name__ == "__main__":
    main()
