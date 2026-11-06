"""예방·법령 Graph 공동 DB 적재 (유종혁 / prevention_legal). 2026-11-05 회의 결정 반영.

적재 대상 (온톨로지 v3.1 노드 6종 중 우리가 쓰는 것)
- 1차 적재(load): Document 4, Chunk 31, SchoolRole 1, ActionConcept 1, SchoolAction 4 (93쪽 항목 1·3·4)
- 확장 적재(add_extension): Chunk 2(b05·b06), SchoolAction 2(a05·a06) — 93쪽 항목 2 (사용자 승인 "a")
- 현재 블록 파일은 33개, 승인 행동은 6개다. TARGET 상수는 1차 적재 기준이다.
- 기존 Disease `MEASLES`, ActionConcept `REQUEST_MEDICAL_VISIT`는 연결만 하고 수정하지 않는다.

안전 장치
- 모든 쓰기는 한 트랜잭션. 검사에서 하나라도 다르면 예외를 내고 전부 되돌린다.
- CREATE 만 쓴다. 같은 ID가 있으면 DB 제약이 실패시킨다 (MERGE 로 기존 노드를 고치지 않는다).
- 개수는 고정된 목표값과 비교한다. 관계는 (시작 ID, 끝 ID) 쌍을 행마다 기대 목록과 대조한다.
  개수만 비교하면 한 곳의 누락이 다른 곳의 중복으로 상쇄될 수 있기 때문이다.
- 법 Chunk 에는 학교 행동 관계를 붙이지 않는다 (적재 계획 5번).
"""
import json
from pathlib import Path

from rag.prevention_legal.neo4j_conn import connect

ROOT = Path(__file__).resolve().parents[3]
BLOCKS_FILE = ROOT / "data" / "interim" / "prevention_legal" / "blocks.jsonl"
STEPS_FILE = ROOT / "data" / "interim" / "prevention_legal" / "steps_raw_v2.jsonl"

# 고정 목표값 (입력에서 계산하지 않는다). 입력이 다르면 적재하지 않는다.
TARGET = {"Document": 4, "Chunk": 31, "SchoolRole": 1, "ActionConcept": 1, "SchoolAction": 4}
EMBEDDING_DIM = 1536

ROLE_ID = "SCHOOL_TEACHER_IN_CHARGE"
ROLE_NAME = "학교[담당교사]"
REPORT_CONCEPT_ID = "REPORT_HEALTH_CENTER"
REPORT_CONCEPT_NAME = "보건소 신고"
REQUEST_CONCEPT_ID = "REQUEST_MEDICAL_VISIT"   # 기존 사전 개념 (연결만)
DISEASE_ID = "MEASLES"                           # 기존 사전 질병 (연결만)

# 문서 메타데이터 (Document 속성). 발행처·판본은 각 지침 표지 기준.
DOC_META = {
    "moleg_infectious_disease_act_21521": {
        "title": "감염병의 예방 및 관리에 관한 법률(법률)(제21521호)",
        "region": "NATIONAL", "version": "2026.10.8 시행본", "issuer": "법제처",
    },
    "kdca_measles_2026": {
        "title": "2026년도 홍역 대응 지침", "region": "NATIONAL",
        "version": "2026", "issuer": "질병관리청",
    },
    "kdca_diagnosis_reporting_2026": {
        "title": "2026년 법정감염병 진단·신고기준 지침", "region": "NATIONAL",
        "version": "2026", "issuer": "질병관리청",
    },
    "kdca_hfmd_2026": {
        "title": "2026년도 엔테로바이러스 감염증·수족구병 관리지침", "region": "NATIONAL",
        "version": "2026", "issuer": "질병관리청",
    },
}

# 승인된 행동이 가리키는 사전 개념. 원문 의미로 고른 것이다 (자동 판정 아님). 키는 action_id.
# a01 진료 안내 -> REQUEST_MEDICAL_VISIT, a02·a03 보건소 신고 -> REPORT_HEALTH_CENTER,
# a04 예방접종 완료 안내 -> HEALTH_EDUCATION (기존 사전, 사용자 승인 2026-11-05)
HEALTH_EDUCATION_ID = "HEALTH_EDUCATION"
CONCEPT_BY_ACTION = {
    "kdca_measles_2026:p0093:a01": REQUEST_CONCEPT_ID,
    "kdca_measles_2026:p0093:a02": REPORT_CONCEPT_ID,
    "kdca_measles_2026:p0093:a03": REPORT_CONCEPT_ID,
    "kdca_measles_2026:p0093:a04": HEALTH_EDUCATION_ID,
    # a05·a06 는 확장 적재(항목 2)에서 쓴다. 개념은 add_extension 의 EXT_CONCEPT 와 같아야 한다.
    "kdca_measles_2026:p0093:a05": "EXCLUDE_FROM_SCHOOL",
    "kdca_measles_2026:p0093:a06": "END_EXCLUSION",
}


def _none_if_text_none(value):
    """원본 파일에 문자열 'None'으로 적힌 빈 값을 실제 빈 값(None)으로 바꾼다."""
    return None if value in (None, "None", "") else value


def build_rows() -> dict:
    """블록 파일과 승인 행으로 적재 행을 만든다. 파일은 읽기만 한다."""
    blocks = [json.loads(l) for l in BLOCKS_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    docs = [{"document_id": d, **meta} for d, meta in DOC_META.items()]
    chunks = [{
        "chunk_id": b["chunk_id"], "document_id": b["document_id"], "text": b["text"],
        "pdf_page": b["pdf_page"], "printed_page": b.get("printed_page"), "section": b.get("section"),
    } for b in blocks]

    # 승인 행만 쓴다 (review_status == approved). 행동 ID 는 쪽 안에서 순서대로 a01, a02 ...
    approved = [json.loads(l) for l in STEPS_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    approved = [r for r in approved if r.get("review_status") == "approved"]
    actions, order_on_page = [], {}
    for r in approved:
        doc_id, page = r["chunk_id"].split(":")[0], int(r["chunk_id"].split(":")[1][1:])
        key = (doc_id, page)
        order_on_page[key] = order_on_page.get(key, 0) + 1
        n = order_on_page[key]
        actions.append({
            "action_id": f"{doc_id}:p{page:04d}:a{n:02d}", "chunk_id": r["chunk_id"],
            "action_text": r["action_text"], "modality": r["modality"], "stage": r["stage"],
            "scope": r["scope"], "step_order": str(n), "timing_text": _none_if_text_none(r.get("timing_text")),
            "when_text": _none_if_text_none(r.get("when_text")), "basis": r["basis"],
            "concept_id": CONCEPT_BY_ACTION[f"{doc_id}:p{page:04d}:a{n:02d}"],
        })
    return {"docs": docs, "chunks": chunks, "actions": actions}


def check_input(data: dict) -> None:
    """입력이 고정 목표값과 맞는지 적재 전에 본다. 맞지 않으면 예외."""
    got = {"Document": len(data["docs"]), "Chunk": len(data["chunks"]),
           "SchoolRole": 1, "ActionConcept": 1, "SchoolAction": len(data["actions"])}
    if got != TARGET:
        raise RuntimeError(f"입력 개수가 목표와 다릅니다: 목표 {TARGET}, 입력 {got}")


def embed_chunks(chunks: list[dict]) -> list[list[float]]:
    """팀 합의 임베딩(text-embedding-3-small, 1536차원)으로 Chunk.text 를 벡터로 바꾼다. 트랜잭션 밖에서 부른다."""
    from dotenv import find_dotenv, load_dotenv

    from index.school.embed import make_embedder  # 팀 모듈 (src/index/school/embed.py)
    load_dotenv(find_dotenv(usecwd=True), override=False)  # 이미 설정된 환경 변수는 덮어쓰지 않는다 (키 값은 출력하지 않음)
    vectors = make_embedder().embed_documents([c["text"] for c in chunks])
    if len(vectors) != len(chunks) or any(len(v) != EMBEDDING_DIM for v in vectors):
        raise RuntimeError("임베딩 개수 또는 차원(1536)이 맞지 않습니다.")
    return vectors


def _expected_pairs(data: dict) -> dict:
    """관계별로 기대하는 (시작 ID, 끝 ID) 쌍 목록. 입력 행에서 한 줄씩 만든다."""
    return {
        "HAS_CHUNK": sorted((c["document_id"], c["chunk_id"]) for c in data["chunks"]),
        "SUPPORTED_BY": sorted((a["action_id"], a["chunk_id"]) for a in data["actions"]),
        "FOR_DISEASE": sorted((a["action_id"], DISEASE_ID) for a in data["actions"]),
        "DONE_BY": sorted((a["action_id"], ROLE_ID) for a in data["actions"]),
        "INSTANCE_OF": sorted((a["action_id"], a["concept_id"]) for a in data["actions"]),
    }


_PAIR_QUERIES = {
    "HAS_CHUNK": "MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE c.chunk_id IN $chunks RETURN d.document_id AS s, c.chunk_id AS t",
    "SUPPORTED_BY": "MATCH (a:SchoolAction)-[:SUPPORTED_BY]->(c:Chunk) WHERE a.action_id IN $actions RETURN a.action_id AS s, c.chunk_id AS t",
    "FOR_DISEASE": "MATCH (a:SchoolAction)-[:FOR_DISEASE]->(d:Disease) WHERE a.action_id IN $actions RETURN a.action_id AS s, d.disease_id AS t",
    "DONE_BY": "MATCH (a:SchoolAction)-[:DONE_BY]->(r:SchoolRole) WHERE a.action_id IN $actions RETURN a.action_id AS s, r.role_id AS t",
    "INSTANCE_OF": "MATCH (a:SchoolAction)-[:INSTANCE_OF]->(k:ActionConcept) WHERE a.action_id IN $actions RETURN a.action_id AS s, k.concept_id AS t",
}


def _verify(tx, data: dict) -> None:
    """노드 개수는 고정 목표와, 관계는 행별 (시작, 끝) 쌍과 비교한다. 다르면 예외."""
    ids = {"docs": [d["document_id"] for d in data["docs"]],
           "chunks": [c["chunk_id"] for c in data["chunks"]],
           "actions": [a["action_id"] for a in data["actions"]]}
    node_queries = {
        "Document": ("MATCH (n:Document) WHERE n.document_id IN $docs RETURN count(n) AS n", {"docs": ids["docs"]}),
        "Chunk": ("MATCH (n:Chunk) WHERE n.chunk_id IN $chunks RETURN count(n) AS n", {"chunks": ids["chunks"]}),
        "SchoolRole": ("MATCH (n:SchoolRole {role_id: $role}) RETURN count(n) AS n", {"role": ROLE_ID}),
        "ActionConcept": ("MATCH (n:ActionConcept {concept_id: $concept}) RETURN count(n) AS n",
                          {"concept": REPORT_CONCEPT_ID}),
        "SchoolAction": ("MATCH (n:SchoolAction) WHERE n.action_id IN $actions RETURN count(n) AS n",
                         {"actions": ids["actions"]}),
    }
    got_nodes = {name: tx.run(q, **p).single()["n"] for name, (q, p) in node_queries.items()}
    if got_nodes != TARGET:
        raise RuntimeError(f"노드 개수 불일치 (롤백): 목표 {TARGET}, 실제 {got_nodes}")

    expected = _expected_pairs(data)
    for rel, pairs in expected.items():
        rows = tx.run(_PAIR_QUERIES[rel], chunks=ids["chunks"], actions=ids["actions"]).data()
        actual = sorted((r["s"], r["t"]) for r in rows)
        if actual != pairs:
            raise RuntimeError(f"{rel} 불일치 (롤백): 기대 {len(pairs)}건, 실제 {len(actual)}건")

    # DONE_BY 의 basis 값도 행마다 맞는지 확인한다
    basis_rows = tx.run("MATCH (a:SchoolAction)-[r:DONE_BY]->(:SchoolRole {role_id: $role}) "
                        "WHERE a.action_id IN $actions RETURN a.action_id AS s, r.basis AS b",
                        role=ROLE_ID, actions=ids["actions"]).data()
    want_basis = sorted((a["action_id"], a["basis"]) for a in data["actions"])
    if sorted((r["s"], r["b"]) for r in basis_rows) != want_basis:
        raise RuntimeError("DONE_BY.basis 불일치 (롤백)")


def load(data: dict, vectors: list[list[float]], *, confirm: bool = False) -> dict:
    """한 트랜잭션으로 적재하고 검사한다. 검사가 실패하면 아무것도 남지 않는다."""
    if not confirm:
        raise RuntimeError("공동 DB 쓰기 함수입니다. 승인 기록을 확인한 뒤 confirm=True 로 호출하세요.")
    check_input(data)
    if len(vectors) != len(data["chunks"]) or any(len(v) != EMBEDDING_DIM for v in vectors):
        raise RuntimeError("임베딩이 입력 Chunk와 맞지 않습니다.")
    for c, v in zip(data["chunks"], vectors):
        c["embedding"] = v

    driver = connect()
    try:
        with driver.session() as s:
            def work(tx):
                # 1) 노드 생성 (CREATE: 같은 ID가 있으면 제약이 실패시킨다)
                tx.run("UNWIND $rows AS r CREATE (:Document {document_id: r.document_id, title: r.title, "
                       "region: r.region, version: r.version, issuer: r.issuer})", rows=data["docs"]).consume()
                tx.run("UNWIND $rows AS r CREATE (:Chunk {chunk_id: r.chunk_id, text: r.text, pdf_page: r.pdf_page, "
                       "printed_page: r.printed_page, section: r.section, embedding: r.embedding})",
                       rows=data["chunks"]).consume()
                tx.run("CREATE (:SchoolRole {role_id: $id, name: $name})", id=ROLE_ID, name=ROLE_NAME).consume()
                tx.run("CREATE (:ActionConcept {concept_id: $id, name: $name})",
                       id=REPORT_CONCEPT_ID, name=REPORT_CONCEPT_NAME).consume()
                tx.run("UNWIND $rows AS r CREATE (:SchoolAction {action_id: r.action_id, action_text: r.action_text, "
                       "modality: r.modality, stage: r.stage, scope: r.scope, step_order: r.step_order, "
                       "timing_text: r.timing_text, when_text: r.when_text})", rows=data["actions"]).consume()

                # 2) 관계 생성. 끝점은 MATCH 로 찾는다. 끝점이 없으면 아래 쌍 검사에서 실패한다.
                tx.run("UNWIND $rows AS r MATCH (d:Document {document_id: r.document_id}), (c:Chunk {chunk_id: r.chunk_id}) "
                       "CREATE (d)-[:HAS_CHUNK]->(c)",
                       rows=[{"document_id": c["document_id"], "chunk_id": c["chunk_id"]} for c in data["chunks"]]).consume()
                tx.run("UNWIND $rows AS r MATCH (a:SchoolAction {action_id: r.action_id}), (c:Chunk {chunk_id: r.chunk_id}) "
                       "CREATE (a)-[:SUPPORTED_BY]->(c)", rows=data["actions"]).consume()
                tx.run("UNWIND $rows AS r MATCH (a:SchoolAction {action_id: r.action_id}), (d:Disease {disease_id: $dis}) "
                       "CREATE (a)-[:FOR_DISEASE]->(d)", rows=data["actions"], dis=DISEASE_ID).consume()
                tx.run("UNWIND $rows AS r MATCH (a:SchoolAction {action_id: r.action_id}), (ro:SchoolRole {role_id: $role}) "
                       "CREATE (a)-[:DONE_BY {basis: r.basis}]->(ro)", rows=data["actions"], role=ROLE_ID).consume()
                tx.run("UNWIND $rows AS r MATCH (a:SchoolAction {action_id: r.action_id}), (k:ActionConcept {concept_id: r.concept_id}) "
                       "CREATE (a)-[:INSTANCE_OF]->(k)", rows=data["actions"]).consume()

                # 3) 검사: 노드 개수와 관계 쌍을 확인한다. 하나라도 다르면 예외 -> 트랜잭션 전체 되돌림
                _verify(tx, data)
                return {"nodes": TARGET, "relations": {k: len(v) for k, v in _expected_pairs(data).items()}}

            return s.execute_write(work)
    finally:
        driver.close()


# 이번에 추가하는 행동 하나 (사용자 승인 2026-11-05, 93쪽 항목 4). 다른 ID 는 받지 않는다.
ADDED_ACTION_ID = "kdca_measles_2026:p0093:a04"


def add_action(action_id: str, *, confirm: bool = False) -> dict:
    """이미 적재된 Document·Chunk 는 그대로 두고, 승인된 행동 1개와 그 관계 4개만 추가한다.

    설명
    - 행동 노드는 CREATE 로 만든다. 같은 action_id 가 있으면 제약이 실패시킨다.
    - 끝점(Chunk, Disease, SchoolRole, ActionConcept)은 새로 만들지 않고 MATCH 로 찾는다.
    - 끝점이 정확히 1개씩 있는지, 관계 쌍과 basis 가 맞는지 확인한다. 다르면 전체 되돌림.
    """
    if not confirm:
        raise RuntimeError("공동 DB 쓰기 함수입니다. 승인 기록을 확인한 뒤 confirm=True 로 호출하세요.")
    if action_id != ADDED_ACTION_ID:
        raise RuntimeError(f"add_action 은 {ADDED_ACTION_ID} 만 받는다 (입력: {action_id})")
    rows = [a for a in build_rows()["actions"] if a["action_id"] == action_id]
    if len(rows) != 1:
        raise RuntimeError(f"승인 행에서 {action_id} 를 찾지 못했습니다.")
    action = rows[0]
    props = ["action_id", "action_text", "modality", "stage", "scope", "step_order", "timing_text", "when_text"]

    driver = connect()
    try:
        with driver.session() as s:
            def work(tx):
                # 끝점이 정확히 하나씩 있어야 한다. 없으면 아래에서 실패한다.
                endpoints = {
                    "chunk": ("MATCH (c:Chunk {chunk_id: $id}) RETURN count(c) AS n", action["chunk_id"]),
                    "disease": ("MATCH (d:Disease {disease_id: $id}) RETURN count(d) AS n", DISEASE_ID),
                    "role": ("MATCH (r:SchoolRole {role_id: $id}) RETURN count(r) AS n", ROLE_ID),
                    "concept": ("MATCH (k:ActionConcept {concept_id: $id}) RETURN count(k) AS n", action["concept_id"]),
                }
                found = {name: tx.run(q, id=value).single()["n"] for name, (q, value) in endpoints.items()}
                if any(n != 1 for n in found.values()):
                    raise RuntimeError(f"끝점 확인 실패 (롤백): {found}")

                tx.run("CREATE (a:SchoolAction) SET a += $props",
                       props={k: action[k] for k in props}).consume()
                tx.run("MATCH (a:SchoolAction {action_id: $aid}), (c:Chunk {chunk_id: $chunk}) "
                       "CREATE (a)-[:SUPPORTED_BY]->(c)", aid=action_id, chunk=action["chunk_id"]).consume()
                tx.run("MATCH (a:SchoolAction {action_id: $aid}), (d:Disease {disease_id: $dis}) "
                       "CREATE (a)-[:FOR_DISEASE]->(d)", aid=action_id, dis=DISEASE_ID).consume()
                tx.run("MATCH (a:SchoolAction {action_id: $aid}), (r:SchoolRole {role_id: $role}) "
                       "CREATE (a)-[:DONE_BY {basis: $basis}]->(r)", aid=action_id, role=ROLE_ID,
                       basis=action["basis"]).consume()
                tx.run("MATCH (a:SchoolAction {action_id: $aid}), (k:ActionConcept {concept_id: $con}) "
                       "CREATE (a)-[:INSTANCE_OF]->(k)", aid=action_id, con=action["concept_id"]).consume()

                # 검사: 이 행동의 관계 쌍, basis, 행동 노드 개수
                want = {
                    "SUPPORTED_BY": [(action_id, action["chunk_id"])],
                    "FOR_DISEASE": [(action_id, DISEASE_ID)],
                    "DONE_BY": [(action_id, ROLE_ID)],
                    "INSTANCE_OF": [(action_id, action["concept_id"])],
                }
                for rel, pairs in want.items():
                    got = sorted((r["s"], r["t"]) for r in tx.run(
                        _PAIR_QUERIES[rel], chunks=[action["chunk_id"]], actions=[action_id]).data())
                    if got != sorted(pairs):
                        raise RuntimeError(f"{rel} 불일치 (롤백): 기대 {pairs}, 실제 {got}")
                # 이 행동에서 나가는 관계는 정확히 4개여야 하고, DONE_BY 는 하나이며 basis 가 맞아야 한다
                out_count = tx.run("MATCH (a:SchoolAction {action_id: $aid})-[r]->() RETURN count(r) AS n",
                                   aid=action_id).single()["n"]
                if out_count != 4:
                    raise RuntimeError(f"행동의 나가는 관계 개수 불일치 (롤백): 기대 4, 실제 {out_count}")
                bases = tx.run("MATCH (a:SchoolAction {action_id: $aid})-[r:DONE_BY]->() RETURN collect(r.basis) AS b",
                               aid=action_id).single()["b"]
                if bases != [action["basis"]]:
                    raise RuntimeError(f"DONE_BY.basis 불일치 (롤백): {bases}")
                basis = action["basis"]
                count = tx.run("MATCH (a:SchoolAction) WHERE a.action_id IN $ids RETURN count(a) AS n",
                               ids=[a["action_id"] for a in build_rows()["actions"]]).single()["n"]
                if count != TARGET["SchoolAction"]:
                    raise RuntimeError(f"행동 개수 불일치 (롤백): 목표 {TARGET['SchoolAction']}, 실제 {count}")
                return {"action_id": action_id, "concept_id": action["concept_id"], "basis": basis,
                        "schoolaction_total": count}

            return s.execute_write(work)
    finally:
        driver.close()


# 확장 적재: 93쪽 항목 2 (사용자 승인 2026-11-05). 이미 적재된 것은 건드리지 않고 아래만 추가한다.
EXT_CHUNK_IDS = ["kdca_measles_2026:p0093:b05", "kdca_measles_2026:p0093:b06"]
EXT_ACTION_IDS = ["kdca_measles_2026:p0093:a05", "kdca_measles_2026:p0093:a06"]
EXT_CONCEPT = {
    "kdca_measles_2026:p0093:a05": "EXCLUDE_FROM_SCHOOL",   # 등교·등원 금지 안내 (사전: 등교중지 실시·통보)
    "kdca_measles_2026:p0093:a06": "END_EXCLUSION",          # 익일 등교 안내 (사전: 등교중지 해제·복귀 확인, 원문은 '익일 등교 안내')
}


def add_extension(*, confirm: bool = False) -> dict:
    """93쪽 항목 2의 Chunk 2개, 행동 2개, 관계 10개(HAS_CHUNK 2, SUPPORTED_BY 2, FOR_DISEASE 2, DONE_BY 2, INSTANCE_OF 2)를 추가한다.
    한 트랜잭션, 검사 실패 시 전부 되돌린다.

    설명
    - Document 는 기존 노드를 MATCH 로 찾는다. 질병·역할·개념 노드도 MATCH 만 한다.
    - Chunk·SchoolAction 은 CREATE 만 쓴다. 같은 ID 가 있으면 제약이 실패시킨다.
    - 임베딩은 트랜잭션 밖에서 만든다 (embed_chunks). 길이와 차원을 확인한다.
    - 검사: 새 노드 개수, 관계 쌍(시작, 끝), basis, 93쪽 행동 총 개수 6, 전체 Chunk 개수 증가분 2.
    """
    if not confirm:
        raise RuntimeError("공동 DB 쓰기 함수입니다. 승인 기록을 확인한 뒤 confirm=True 로 호출하세요.")
    blocks = {c["chunk_id"]: c for c in build_rows()["chunks"]}
    actions = {a["action_id"]: a for a in build_rows()["actions"]}
    chunks = [blocks[cid] for cid in EXT_CHUNK_IDS]
    new_actions = [actions[aid] for aid in EXT_ACTION_IDS]
    for a in new_actions:
        a["concept_id"] = EXT_CONCEPT[a["action_id"]]

    vectors = embed_chunks(chunks)   # 트랜잭션 밖에서 임베딩한다
    for c, v in zip(chunks, vectors):
        c["embedding"] = v

    driver = connect()
    try:
        with driver.session() as s:
            def work(tx):
                # 끝점 확인: Document, Disease, SchoolRole, ActionConcept 은 각각 정확히 1개여야 한다
                ends = {
                    "doc": ("MATCH (d:Document {document_id: $id}) RETURN count(d) AS n", "kdca_measles_2026"),
                    "disease": ("MATCH (d:Disease {disease_id: $id}) RETURN count(d) AS n", DISEASE_ID),
                    "role": ("MATCH (r:SchoolRole {role_id: $id}) RETURN count(r) AS n", ROLE_ID),
                }
                for a in new_actions:
                    ends["concept:" + a["action_id"]] = (
                        "MATCH (k:ActionConcept {concept_id: $id}) RETURN count(k) AS n", a["concept_id"])
                found = {k: tx.run(q, id=v).single()["n"] for k, (q, v) in ends.items()}
                if any(n != 1 for n in found.values()):
                    raise RuntimeError(f"끝점 확인 실패 (롤백): {found}")

                # 0) 전체 Chunk 개수를 먼저 센다 (증가분 검사에 쓴다)
                chunk_before = tx.run("MATCH (c:Chunk) RETURN count(c) AS n").single()["n"]

                # 1) Chunk 생성 (CREATE) 과 Document 연결
                tx.run("UNWIND $rows AS r CREATE (:Chunk {chunk_id: r.chunk_id, text: r.text, pdf_page: r.pdf_page, "
                       "printed_page: r.printed_page, section: r.section, embedding: r.embedding})",
                       rows=chunks).consume()
                tx.run("UNWIND $rows AS r MATCH (d:Document {document_id: r.document_id}), (c:Chunk {chunk_id: r.chunk_id}) "
                       "CREATE (d)-[:HAS_CHUNK]->(c)", rows=chunks).consume()

                # 2) SchoolAction 생성 (CREATE)
                tx.run("UNWIND $rows AS r CREATE (:SchoolAction {action_id: r.action_id, action_text: r.action_text, "
                       "modality: r.modality, stage: r.stage, scope: r.scope, step_order: r.step_order, "
                       "timing_text: r.timing_text, when_text: r.when_text})", rows=new_actions).consume()

                # 3) 관계 생성 (끝점은 MATCH)
                tx.run("UNWIND $rows AS r MATCH (a:SchoolAction {action_id: r.action_id}), (c:Chunk {chunk_id: r.chunk_id}) "
                       "CREATE (a)-[:SUPPORTED_BY]->(c)", rows=new_actions).consume()
                tx.run("UNWIND $rows AS r MATCH (a:SchoolAction {action_id: r.action_id}), (d:Disease {disease_id: $dis}) "
                       "CREATE (a)-[:FOR_DISEASE]->(d)", rows=new_actions, dis=DISEASE_ID).consume()
                tx.run("UNWIND $rows AS r MATCH (a:SchoolAction {action_id: r.action_id}), (ro:SchoolRole {role_id: $role}) "
                       "CREATE (a)-[:DONE_BY {basis: r.basis}]->(ro)", rows=new_actions, role=ROLE_ID).consume()
                tx.run("UNWIND $rows AS r MATCH (a:SchoolAction {action_id: r.action_id}), (k:ActionConcept {concept_id: r.concept_id}) "
                       "CREATE (a)-[:INSTANCE_OF]->(k)", rows=new_actions).consume()

                # 4) 검사
                # 4-1) 새 노드 개수
                n_chunk = tx.run("MATCH (c:Chunk) WHERE c.chunk_id IN $ids RETURN count(c) AS n",
                                 ids=EXT_CHUNK_IDS).single()["n"]
                n_act = tx.run("MATCH (a:SchoolAction) WHERE a.action_id IN $ids RETURN count(a) AS n",
                               ids=EXT_ACTION_IDS).single()["n"]
                if (n_chunk, n_act) != (2, 2):
                    raise RuntimeError(f"새 노드 개수 불일치 (롤백): chunk {n_chunk}, action {n_act}")
                chunk_after = tx.run("MATCH (c:Chunk) RETURN count(c) AS n").single()["n"]
                if chunk_after - chunk_before != 2:
                    raise RuntimeError(f"전체 Chunk 증가분 불일치 (롤백): {chunk_after - chunk_before}")
                # 4-2) 관계 쌍 (새 행동 기준)
                want = {
                    "HAS_CHUNK": sorted(("kdca_measles_2026", cid) for cid in EXT_CHUNK_IDS),
                    "SUPPORTED_BY": sorted((a["action_id"], a["chunk_id"]) for a in new_actions),
                    "FOR_DISEASE": sorted((a["action_id"], DISEASE_ID) for a in new_actions),
                    "DONE_BY": sorted((a["action_id"], ROLE_ID) for a in new_actions),
                    "INSTANCE_OF": sorted((a["action_id"], a["concept_id"]) for a in new_actions),
                }
                for rel, pairs in want.items():
                    got = sorted((r["s"], r["t"]) for r in tx.run(
                        _PAIR_QUERIES[rel], chunks=EXT_CHUNK_IDS, actions=EXT_ACTION_IDS).data())
                    if got != pairs:
                        raise RuntimeError(f"{rel} 불일치 (롤백): 기대 {pairs}, 실제 {got}")
                # 4-3) basis: 새 행동의 DONE_BY 가 모두 EXPLICIT 이어야 하고, 행동에서 나가는 관계는 행동당 4개
                for a in new_actions:
                    out_count = tx.run("MATCH (a:SchoolAction {action_id: $aid})-[r]->() RETURN count(r) AS n",
                                       aid=a["action_id"]).single()["n"]
                    bases = tx.run("MATCH (a:SchoolAction {action_id: $aid})-[r:DONE_BY]->() RETURN collect(r.basis) AS b",
                                   aid=a["action_id"]).single()["b"]
                    if out_count != 4 or bases != [a["basis"]]:
                        raise RuntimeError(f"행동 {a['action_id']} 관계 검사 실패 (롤백): 나가는 관계 {out_count}, basis {bases}")
                # 4-4) 93쪽 행동 총 개수는 6
                total = tx.run("MATCH (a:SchoolAction) WHERE a.action_id STARTS WITH 'kdca_measles_2026:p0093' "
                               "RETURN count(a) AS n").single()["n"]
                if total != 6:
                    raise RuntimeError(f"93쪽 행동 총 개수 불일치 (롤백): 기대 6, 실제 {total}")
                return {"chunks_added": n_chunk, "actions_added": n_act, "measles_93_actions": total,
                        "relations_added": sum(len(v) for v in want.values())}

            return s.execute_write(work)
    finally:
        driver.close()


# 93쪽 항목 5 청크 1개 (사용자 승인 2026-11-06 "적재해"). 행동·카드는 만들지 않는다. 독립 A 문항 평가용.
ADDED_CHUNK_ID = "kdca_measles_2026:p0093:b07"


def add_chunk_only(*, confirm: bool = False) -> dict:
    """ADDED_CHUNK_ID 청크 1개를 Document 와 HAS_CHUNK 로 연결해 추가한다. 행동·관계(행동 쪽)는 만들지 않는다.

    설명
    - 청크는 CREATE 로 만든다. 같은 ID 가 있으면 제약이 실패시킨다.
    - 전체 Chunk 개수가 정확히 1 늘었는지, HAS_CHUNK 가 1개 생겼는지 확인한다. 다르면 전체 되돌림.
    - 임베딩은 트랜잭션 밖에서 만든다.
    """
    if not confirm:
        raise RuntimeError("공동 DB 쓰기 함수입니다. 승인 기록을 확인한 뒤 confirm=True 로 호출하세요.")
    chunk = next(c for c in build_rows()["chunks"] if c["chunk_id"] == ADDED_CHUNK_ID)
    vectors = embed_chunks([chunk])
    chunk["embedding"] = vectors[0]

    driver = connect()
    try:
        with driver.session() as s:
            def work(tx):
                before = tx.run("MATCH (c:Chunk) RETURN count(c) AS n").single()["n"]
                tx.run("CREATE (c:Chunk {chunk_id: $id, text: $text, pdf_page: $page, printed_page: $printed, "
                       "section: $section, embedding: $emb})", id=chunk["chunk_id"], text=chunk["text"],
                       page=chunk["pdf_page"], printed=chunk["printed_page"], section=chunk["section"],
                       emb=chunk["embedding"]).consume()
                tx.run("MATCH (d:Document {document_id: $doc}), (c:Chunk {chunk_id: $id}) "
                       "CREATE (d)-[:HAS_CHUNK]->(c)", doc=chunk["document_id"], id=chunk["chunk_id"]).consume()
                after = tx.run("MATCH (c:Chunk) RETURN count(c) AS n").single()["n"]
                links = tx.run("MATCH (:Document)-[r:HAS_CHUNK]->(c:Chunk {chunk_id: $id}) RETURN count(r) AS n",
                               id=chunk["chunk_id"]).single()["n"]
                if after - before != 1 or links != 1:
                    raise RuntimeError(f"검사 실패 (롤백): 증가 {after - before}, 연결 {links}")
                return {"chunk_id": chunk["chunk_id"], "chunk_total_before": before, "chunk_total_after": after}

            return s.execute_write(work)
    finally:
        driver.close()


# 2026-11-06 승인 (추천안): Q15 근거(홍역 지침 116쪽 학교 대상 임시예방접종 행 3개)와 Q23 근거(예방접종 지침 116쪽 표 32 행 7개).
PAGE116_CHUNK_IDS = [f"kdca_measles_2026:p0116:t01r0{i}" for i in range(1, 4)] + \
                    [f"kdca_vaccine_preventable_2026:p0116:t01r0{i}" for i in range(1, 8)]
VACCINE_DOC = {"document_id": "kdca_vaccine_preventable_2026",
               "title": "2026년도 예방접종 대상 감염병 관리 지침", "region": "NATIONAL",
               "version": "2026", "issuer": "질병관리청"}


def add_page116_blocks(*, confirm: bool = False) -> dict:
    """116쪽 블록 10개(Chunk 10)와 예방접종 지침 문서 노드(Document 1)를 추가한다. 관계는 HAS_CHUNK 10 뿐이다.

    설명
    - 쓰기 전에 같은 ID 가 있는지 트랜잭션 안에서 다시 확인한다. 있으면 전체 되돌림.
    - 검사는 이번에 만든 ID 목록만 센다. 전체 개수 증가분으로 판단하지 않는다.
    - HAS_CHUNK 는 (문서, 청크) 쌍을 기대값과 정확히 비교한다.
    """
    if not confirm:
        raise RuntimeError("공동 DB 쓰기 함수입니다. 승인 기록을 확인한 뒤 confirm=True 로 호출하세요.")
    rows = {c["chunk_id"]: c for c in build_rows()["chunks"]}
    chunks = [rows[cid] for cid in PAGE116_CHUNK_IDS]
    vectors = embed_chunks(chunks)
    for c, v in zip(chunks, vectors):
        c["embedding"] = v
    expected_pairs = sorted((c["document_id"], c["chunk_id"]) for c in chunks)

    driver = connect()
    try:
        with driver.session() as s:
            def work(tx):
                clash_doc = tx.run("MATCH (d:Document {document_id: $id}) RETURN count(d) AS n",
                                   id=VACCINE_DOC["document_id"]).single()["n"]
                clash_chunk = tx.run("MATCH (c:Chunk) WHERE c.chunk_id IN $ids RETURN count(c) AS n",
                                     ids=PAGE116_CHUNK_IDS).single()["n"]
                if clash_doc or clash_chunk:
                    raise RuntimeError(f"ID 충돌 (롤백): 문서 {clash_doc}, 청크 {clash_chunk}")

                tx.run("CREATE (:Document {document_id: $document_id, title: $title, region: $region, "
                       "version: $version, issuer: $issuer})", **VACCINE_DOC).consume()
                tx.run("UNWIND $rows AS r CREATE (:Chunk {chunk_id: r.chunk_id, text: r.text, pdf_page: r.pdf_page, "
                       "printed_page: r.printed_page, section: r.section, embedding: r.embedding})",
                       rows=chunks).consume()
                tx.run("UNWIND $rows AS r MATCH (d:Document {document_id: r.document_id}), (c:Chunk {chunk_id: r.chunk_id}) "
                       "CREATE (d)-[:HAS_CHUNK]->(c)", rows=chunks).consume()

                n_doc = tx.run("MATCH (d:Document {document_id: $id}) RETURN count(d) AS n",
                               id=VACCINE_DOC["document_id"]).single()["n"]
                n_chunk = tx.run("MATCH (c:Chunk) WHERE c.chunk_id IN $ids RETURN count(c) AS n",
                                 ids=PAGE116_CHUNK_IDS).single()["n"]
                pairs = sorted((r["d"], r["c"]) for r in tx.run(
                    "MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE c.chunk_id IN $ids "
                    "RETURN d.document_id AS d, c.chunk_id AS c", ids=PAGE116_CHUNK_IDS).data())
                if (n_doc, n_chunk) != (1, 10) or pairs != expected_pairs:
                    raise RuntimeError(f"검사 실패 (롤백): 문서 {n_doc}, 청크 {n_chunk}, 쌍 {len(pairs)}")
                return {"documents": n_doc, "chunks": n_chunk, "has_chunk_pairs": len(pairs)}

            return s.execute_write(work)
    finally:
        driver.close()
