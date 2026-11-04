"""B05: 정규화·ID 배정이 끝난 행동과 공식 청크를 Neo4j 적재 패키지로 바꾼다.

참고: school_learning_reference.ipynb R05(to_rows·make_props·check_sc40, ref_build_bundle).
- 노드 속성은 온톨로지 v3.1 허용 목록만 투영한다. 검수 상태·후보 지문·모델·해시는 넣지 않는다.
- 관계는 6개 허용 패턴만, DONE_BY는 basis 필수.
- COMMON/DISEASE_GROUP/UNKNOWN 행동에는 FOR_DISEASE를 만들지 않는다.
- 사전 노드(Disease·SchoolRole·ActionConcept)는 적재 패키지에 넣지 않는다. 공통 초기화로 먼저
  존재해야 하는 키 목록(dictionary_refs)만 내보내고, 적재기(B06)는 MATCH로 연결한다.
"""

from __future__ import annotations

from index.school.ids_local import IdError, parse_id
from index.school.normalize import BASES

NODE_PROPERTIES = {
    "Document": ("document_id", "title", "issuer", "version", "region", "source_url"),
    "Chunk": ("chunk_id", "text", "pdf_page", "printed_page", "section"),
    "SchoolAction": ("action_id", "action_text", "scope", "stage", "step_order", "when_text",
                     "timing_text", "modality"),
}
ID_FIELD = {"Document": "document_id", "Chunk": "chunk_id", "SchoolAction": "action_id",
            "Disease": "disease_id", "SchoolRole": "role_id", "ActionConcept": "concept_id"}
DICTIONARY_LABELS = {"Disease": "disease", "SchoolRole": "role", "ActionConcept": "concept"}
ALLOWED_EDGES = {
    ("Document", "HAS_CHUNK", "Chunk"), ("Chunk", "ABOUT", "Disease"),
    ("SchoolAction", "SUPPORTED_BY", "Chunk"), ("SchoolAction", "FOR_DISEASE", "Disease"),
    ("SchoolAction", "DONE_BY", "SchoolRole"), ("SchoolAction", "INSTANCE_OF", "ActionConcept"),
}
ID_KINDS = {"Document": {"document"}, "Chunk": {"chunk_block", "chunk_row"},
            "SchoolAction": {"action"}}
CONCEPT_POLICIES = {"hold", "omit_link"}


class BundleError(ValueError):
    """적재 패키지 규칙 위반."""


def _edge(start_label, start_id, rel, end_label, end_id, props=None) -> dict:
    return {"type": rel, "start_label": start_label, "start_id": start_id,
            "end_label": end_label, "end_id": end_id, "props": props or {}}


def edge_key(edge: dict) -> tuple:
    """관계 비교 키. DONE_BY는 basis까지 비교한다(B06 실제 키 검증과 같은 모양)."""
    return (edge["start_label"], edge["start_id"], edge["type"], edge["end_label"],
            edge["end_id"], edge["props"].get("basis"))


def build_load_bundle(actions: list[dict], official_chunks: list[dict], documents: list[dict],
                      books: dict, *, concept_policy: str = "omit_link",
                      chunk_diseases: dict[str, list[str]] | None = None) -> dict:
    """적재 패키지를 만들고 validate_bundle을 통과한 경우에만 돌려준다.

    concept_policy
    - "omit_link"(기본): 맞는 표준 개념이 없으면 행동은 적재하고 INSTANCE_OF만 만들지 않는다.
      개념은 문서 간 비교용이며 필수 관계가 아니다(온톨로지 v3.1 ⑥·7절 '억지로 고르지 않는다').
    - "hold": 개념이 확정되지 않은 행동은 적재하지 않고 deferred에 남긴다.
    chunk_diseases: {chunk_id: [disease_id]} - 그 청크(표 행)가 실제로 다루는 질병만. 기본은 없음.
    """
    if concept_policy not in CONCEPT_POLICIES:
        raise BundleError(f"알 수 없는 개념 정책: {concept_policy}")
    chunk_diseases = chunk_diseases or {}
    bundle = {"nodes": {label: [] for label in NODE_PROPERTIES}, "edges": [],
              "deferred": [], "warnings": [], "policy": {"concept_policy": concept_policy}}
    used_documents = {c["document_id"] for c in official_chunks}
    for document in sorted(documents, key=lambda d: d["document_id"]):
        if document["document_id"] in used_documents:
            bundle["nodes"]["Document"].append({k: document[k] for k in NODE_PROPERTIES["Document"]})
    for chunk in sorted(official_chunks, key=lambda c: c["chunk_id"]):
        bundle["nodes"]["Chunk"].append({k: chunk[k] for k in NODE_PROPERTIES["Chunk"]})
        bundle["edges"].append(_edge("Document", chunk["document_id"], "HAS_CHUNK", "Chunk",
                                     chunk["chunk_id"]))
        for disease_id in chunk_diseases.get(chunk["chunk_id"], []):
            bundle["edges"].append(_edge("Chunk", chunk["chunk_id"], "ABOUT", "Disease", disease_id))

    for action in sorted(actions, key=lambda a: a["action_id"]):
        concept = action["concept"]
        if concept["status"] != "RESOLVED" and concept_policy == "hold":
            # 적재하지 않는 행동의 요약만 남긴다(전체 레코드는 정규화 JSONL에 있음).
            bundle["deferred"].append({
                "action_id": action["action_id"], "candidate_key": action["candidate_key"],
                "action_text": action["action_text"],
                "hold_reasons": [f"개념 미확정({concept['status']}) - 개념 정책 hold"]})
            continue
        bundle["nodes"]["SchoolAction"].append({k: action[k] for k in NODE_PROPERTIES["SchoolAction"]})
        aid = action["action_id"]
        bundle["edges"].append(_edge("SchoolAction", aid, "SUPPORTED_BY", "Chunk", action["chunk_id"]))
        for role in action["roles"]:
            bundle["edges"].append(_edge("SchoolAction", aid, "DONE_BY", "SchoolRole",
                                         role["role_id"], {"basis": role["basis"]}))
        if concept["status"] == "RESOLVED":
            bundle["edges"].append(_edge("SchoolAction", aid, "INSTANCE_OF", "ActionConcept",
                                         concept["id"]))
        else:
            bundle["warnings"].append(f"{aid}: 개념 미확정({concept['status']}) - INSTANCE_OF 생략")
        if action["scope"] == "NAMED_DISEASES":
            for disease_id in action["disease_ids"]:
                bundle["edges"].append(_edge("SchoolAction", aid, "FOR_DISEASE", "Disease",
                                             disease_id))

    validate_bundle(bundle, books)
    bundle["dictionary_refs"] = sorted({(e["end_label"], e["end_id"]) for e in bundle["edges"]
                                        if e["end_label"] in DICTIONARY_LABELS})
    bundle["expected"] = expected_keys(bundle)
    return bundle


def expected_keys(bundle: dict) -> dict:
    """B06에서 실제 DB 조회 결과와 비교할 기대 키 집합(정렬된 목록)."""
    nodes = sorted((label, row[ID_FIELD[label]]) for label, rows in bundle["nodes"].items()
                   for row in rows)
    return {"nodes": nodes, "edges": sorted(edge_key(e) for e in bundle["edges"])}


def validate_bundle(bundle: dict, books: dict) -> None:
    """허용 속성·ID 형식·관계 패턴·basis·끝점 존재·중복을 검사한다. 위반을 모두 모아 알린다."""
    problems: list[str] = []
    present: dict[str, set[str]] = {}
    for label, rows in bundle["nodes"].items():
        if label not in NODE_PROPERTIES:
            problems.append(f"적재 패키지에 넣을 수 없는 노드 라벨: {label}")
            continue
        ids = set()
        for row in rows:
            if set(row) != set(NODE_PROPERTIES[label]):
                problems.append(f"{label} 속성 목록 위반: {sorted(set(row) ^ set(NODE_PROPERTIES[label]))}")
            ident = row.get(ID_FIELD[label])
            try:
                kind = parse_id(ident)["kind"]
                if kind not in ID_KINDS[label]:
                    problems.append(f"{label} ID 종류 오류: {ident}")
            except IdError as exc:
                problems.append(str(exc))
            if ident in ids:
                problems.append(f"{label} 중복 ID: {ident}")
            ids.add(ident)
        present[label] = ids
    for label, kind in DICTIONARY_LABELS.items():
        present[label] = {e[ID_FIELD[label]] for e in books[kind]["entries"]}

    chunk_doc = {c["chunk_id"]: parse_id(c["chunk_id"]) for c in bundle["nodes"].get("Chunk", [])
                 if c["chunk_id"] in present.get("Chunk", set())}
    seen, has_chunk, supported = set(), {}, {}
    for edge in bundle["edges"]:
        pattern = (edge["start_label"], edge["type"], edge["end_label"])
        if pattern not in ALLOWED_EDGES:
            problems.append(f"허용하지 않은 관계: {pattern}")
            continue
        for side in ("start", "end"):
            label, ident = edge[f"{side}_label"], edge[f"{side}_id"]
            if ident not in present.get(label, set()):
                problems.append(f"없는 끝점: {label}:{ident} ({edge['type']})")
        if edge["type"] == "DONE_BY":
            if set(edge["props"]) != {"basis"} or edge["props"]["basis"] not in BASES:
                problems.append(f"DONE_BY basis 누락·허용값 위반: {edge['start_id']}")
        elif edge["props"]:
            problems.append(f"{edge['type']}에는 관계 속성을 두지 않음")
        key = (edge["start_id"], edge["type"], edge["end_id"])
        if key in seen:
            problems.append(f"중복 관계: {key}")
        seen.add(key)
        if edge["type"] == "HAS_CHUNK":
            has_chunk.setdefault(edge["end_id"], []).append(edge["start_id"])
        if edge["type"] == "SUPPORTED_BY":
            supported.setdefault(edge["start_id"], []).append(edge["end_id"])

    for chunk_id, parsed in chunk_doc.items():
        owners = has_chunk.get(chunk_id, [])
        if owners != [parsed["document_id"]]:
            problems.append(f"청크의 HAS_CHUNK가 자기 문서 하나가 아님: {chunk_id} {owners}")
    scopes = {a["action_id"]: a["scope"] for a in bundle["nodes"].get("SchoolAction", [])}
    for action in bundle["nodes"].get("SchoolAction", []):
        aid = action["action_id"]
        parsed = parse_id(aid) if aid in present["SchoolAction"] else None
        chunks = supported.get(aid, [])
        if not chunks:
            problems.append(f"근거 청크 없는 행동: {aid}")
        if parsed and chunks:
            first = chunk_doc.get(chunks[0])
            if first and (first["document_id"], first["page"]) != (parsed["document_id"], parsed["page"]):
                problems.append(f"행동 ID의 문서·쪽이 주 근거 청크와 다름: {aid}")
            if any(chunk_doc.get(c, {}).get("document_id") != parsed["document_id"] for c in chunks):
                problems.append(f"다른 문서의 청크를 근거로 가진 행동: {aid}")
    for edge in bundle["edges"]:
        if edge["type"] == "FOR_DISEASE" and scopes.get(edge["start_id"]) != "NAMED_DISEASES":
            problems.append(f"NAMED_DISEASES가 아닌 행동의 FOR_DISEASE: {edge['start_id']}")
    for label, rows in bundle["nodes"].items():
        for row in rows:
            for value in row.values():
                if isinstance(value, str) and value.startswith("TEMP:"):
                    problems.append(f"TEMP 값이 적재 패키지에 있음: {label}")
    if problems:
        raise BundleError("적재 패키지 검사 실패:\n- " + "\n- ".join(dict.fromkeys(problems)))
