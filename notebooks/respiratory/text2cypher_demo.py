"""운영 Tool과 분리된 프로젝트 Aura Text2Cypher 시연 부품. DB 쓰기 없음."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from importlib import metadata
from itertools import islice
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from neo4j import READ_ACCESS, GraphDatabase, Query, unit_of_work
from neo4j.graph import Node, Relationship
from neo4j.graph import Path as GraphPath

from rag.respiratory.graph import PROJECT_ROOT
from rag.respiratory.text2cypher_tool import _clean_cypher, _code_mask

DIRECTORY = Path(__file__).resolve().parent
SCHEMA_PATH = DIRECTORY / "text2cypher_schema.json"
MAX_ROWS, MAX_NODES, MAX_EDGES = 50, 100, 150
LABEL_KEYS = {
    "Document": "document_id", "Chunk": "chunk_id", "SchoolAction": "action_id",
    "Disease": "disease_id", "SchoolRole": "role_id", "ActionConcept": "concept_id",
}
PATTERNS = {
    ("Document", "HAS_CHUNK", "Chunk"), ("Chunk", "ABOUT", "Disease"),
    ("SchoolAction", "SUPPORTED_BY", "Chunk"), ("SchoolAction", "FOR_DISEASE", "Disease"),
    ("SchoolAction", "DONE_BY", "SchoolRole"),
    ("SchoolAction", "INSTANCE_OF", "ActionConcept"),
}
SEMANTICS = [
    "pdf_page는 PDF 뷰어 기준 1부터, printed_page는 인쇄 쪽수이며 null일 수 있음.",
    "FOR_DISEASE 없는 COMMON/DISEASE_GROUP 행동도 있음. 질병별 관계 검색을 전체 학교 행동으로 일반화하지 않음.",
    "ABOUT은 청크의 주제이며, 연결된 행동의 FOR_DISEASE나 의무를 자동으로 뜻하지 않음.",
    "DONE_BY.basis=EXAMPLE은 역할 예시이며 확정 의무가 아님.",
    "잠복기·증상·기간은 Disease 속성이 아님. Chunk 원문에서 확인해야 함.",
    "그래프는 명시적 DB 관계만 반환. 노드 속성명 합집합은 모든 노드의 속성 존재를 뜻하지 않음.",
]
EXAMPLES = [
    ("보호자 연락 행동과 근거 문서를 그래프로 보여줘",
     ("MATCH path=(doc:Document)-[:HAS_CHUNK]->(chunk:Chunk)<-[:SUPPORTED_BY]-"
     "(action:SchoolAction)-[:INSTANCE_OF]->"
     "(:ActionConcept {concept_id:'CONTACT_GUARDIAN'}) "
      "RETURN path ORDER BY doc.document_id, action.action_id LIMIT 50")),
    ("성홍열 관련 학교 행동과 근거 문서를 그래프로 보여줘",
     ("MATCH path=(doc:Document)-[:HAS_CHUNK]->(chunk:Chunk)<-[:SUPPORTED_BY]-"
     "(action:SchoolAction)-[:FOR_DISEASE]->(:Disease {disease_id:'SCARLET_FEVER'}) "
      "RETURN path ORDER BY doc.document_id, action.action_id LIMIT 50")),
    ("문서별 청크 수는?",
     ("MATCH (doc:Document) OPTIONAL MATCH (doc)-[:HAS_CHUNK]->(chunk:Chunk) "
     "RETURN doc.document_id AS document_id,count(DISTINCT chunk) AS chunk_count "
      "ORDER BY document_id LIMIT 50")),
]


def now():
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat()


def versions():
    result = {}
    for name in ("neo4j", "langchain-openai", "neo4j-graphrag", "ipywidgets", "nbformat"):
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = None
    return result


def safe_error(error):
    code = getattr(error, "code", None)
    return {"type": type(error).__name__,
            "code": code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.]+", code) else None}


def connect_project():
    """기존 프로젝트 설정 사용. URI·계정·비밀번호는 반환·출력하지 않는다."""
    import truststore
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    truststore.inject_into_ssl()
    uri = os.environ["NEO4J_URI"]
    parsed = urlparse(uri)
    if parsed.scheme != "neo4j+s" or not parsed.hostname or not parsed.hostname.endswith(".databases.neo4j.io"):
        raise ValueError("프로젝트 Aura의 인증서 검증 TLS 연결이 필요함")
    readonly_user = os.getenv("NEO4J_READONLY_USERNAME") or os.getenv("NEO4J_T2C_USERNAME")
    readonly_password = os.getenv("NEO4J_READONLY_PASSWORD") or os.getenv("NEO4J_T2C_PASSWORD")
    if bool(readonly_user) != bool(readonly_password):
        raise ValueError("읽기 전용 계정 설정은 사용자와 비밀번호가 함께 필요함")
    driver = GraphDatabase.driver(
        uri, auth=(readonly_user or os.environ["NEO4J_USERNAME"],
                   readonly_password or os.environ["NEO4J_PASSWORD"]),
        connection_timeout=10, connection_acquisition_timeout=10, max_transaction_retry_time=0,
        notifications_disabled_classifications=["DEPRECATION", "UNRECOGNIZED"],
    )
    try:
        driver.verify_connectivity()
    except Exception:
        driver.close()
        raise
    return driver, os.getenv("NEO4J_DATABASE") or None, {
        "instance_alias": "team2-project-aura",
        "instance_fingerprint": hashlib.sha256(parsed.hostname.encode()).hexdigest()[:12],
        "database": os.getenv("NEO4J_DATABASE") or "server-default",
        "tls_certificate_verified": True, "dedicated_readonly_settings": bool(readonly_user),
    }


def _metadata_rows(driver, database, query):
    with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        return [row.data() for row in session.run(Query(query, timeout=10))]


def account_report(driver, database):
    report = {"readonly_account_verified": False, "role_query_status": "UNAVAILABLE",
              "privilege_query_status": "UNAVAILABLE", "write_capable_role_observed": None}
    try:
        rows = _metadata_rows(driver, database, "SHOW CURRENT USER YIELD roles RETURN roles")
        roles = set(rows[0]["roles"])
        report.update(role_query_status="OK", write_capable_role_observed=any(
            "admin" in role.lower() or role.lower() in {"publisher", "architect", "editor"}
            for role in roles))
        report["readonly_account_verified"] = bool(roles) and roles <= {"reader", "PUBLIC"}
    except Exception as error:  # noqa: BLE001 -- 서버 버전·권한 차이를 비밀 없이 기록
        report["role_error"] = safe_error(error)
    try:
        rows = _metadata_rows(driver, database, "SHOW USER PRIVILEGES")
        actions = sorted({str(row.get("action", "")).lower() for row in rows
                          if str(row.get("access", "")).upper() == "GRANTED"})
        report.update(privilege_query_status="OK", granted_actions=actions)
    except Exception as error:  # noqa: BLE001 -- 서버 버전·권한 차이를 비밀 없이 기록
        report["privilege_error"] = safe_error(error)
    report["execution_policy"] = "사용자 요청에 따라 기존 계정 사용; 계정 권한과 별도로 프로그램의 조회 검사를 강제"
    return report


def collect_schema(driver, database, connection):
    """구조와 작은 값 사전만 읽고 본문·embedding 값은 수집하지 않는다."""
    properties = _metadata_rows(driver, database,
        "MATCH (n) UNWIND labels(n) AS label UNWIND keys(n) AS property "
        "RETURN DISTINCT label,property ORDER BY label,property")
    labels = _metadata_rows(driver, database, "CALL db.labels() YIELD label RETURN label ORDER BY label")
    patterns = _metadata_rows(driver, database,
        "MATCH (s)-[r]->(t) RETURN DISTINCT labels(s) AS source_labels,type(r) AS relationship_type,"
        "labels(t) AS target_labels,keys(r) AS relationship_properties ORDER BY relationship_type")
    observed_properties = {label: sorted({r["property"] for r in properties
                           if r["label"] == label and r["property"] != "embedding"})
                           for label in LABEL_KEYS if any(r["label"] == label for r in labels)}
    dictionaries = {}
    for label, key in LABEL_KEYS.items():
        if label in {"Disease", "SchoolRole"}:
            query = f"MATCH (n:{label}) RETURN n.{key} AS id,n.name AS name,n.aliases AS aliases ORDER BY id"
        elif label == "ActionConcept":
            query = "MATCH (n:ActionConcept) RETURN n.concept_id AS id,n.name AS name ORDER BY id"
        elif label == "Document":
            query = "MATCH (n:Document) RETURN n.document_id AS id,n.title AS title,n.region AS region ORDER BY id"
        else:
            continue
        dictionaries[label] = _metadata_rows(driver, database, query)
    values = _metadata_rows(driver, database,
        "MATCH (a:SchoolAction) RETURN DISTINCT a.scope AS scope,a.stage AS stage,a.modality AS modality")
    basis = _metadata_rows(driver, database,
        "MATCH (:SchoolAction)-[r:DONE_BY]->(:SchoolRole) RETURN r.basis AS basis,count(*) AS relationship_count ORDER BY basis")
    included, excluded = [], []
    for row in patterns:
        possible = {(s, row["relationship_type"], t) for s in row["source_labels"] for t in row["target_labels"]}
        (included if possible and possible <= PATTERNS else excluded).append(row)
    report = {
        "schema_version": "text2cypher-aura-20261109-v1", "observed_at_kst": now(),
        "connection": connection, "libraries": versions(), "properties": observed_properties,
        "relationships": included, "excluded_labels": sorted({r["label"] for r in labels} - LABEL_KEYS.keys()),
        "excluded_patterns": excluded, "dictionaries": dictionaries,
        "action_values": values, "done_by_basis": basis, "semantics": SEMANTICS,
        "semantic_sources": ["교육시설_감염병_GraphRAG_온톨로지_스키마_설명서_v3.1.md",
                             "notebooks/respiratory/visual_queries.cypher"],
        "differences": {"ActionConcept.group_observed": "group" in observed_properties.get("ActionConcept", []),
                        "embedding": "존재 여부와 관계없이 프롬프트·로그에서 제외",
                        "all_properties_are_union_not_required": True},
        "account": account_report(driver, database),
        "scope": "프로젝트 Aura의 현재 Document 전체. 문서별 접근 제한 기능을 구현한 것은 아님.",
        "browser_verification": {"status": "NOT_VERIFIED", "reason": "Chrome 컴퓨터 제어 미승인; 실제 Aura 화면에서 대조 필요"},
        "retriever_reuse": {"installed": versions()["neo4j-graphrag"] is not None,
                           "method_signature": None,
                           "method": "제공된 수업 흐름의 생성→EXPLAIN→query_type r→실행 단계를 분리 적용"},
    }
    return report


def build_schema_context(schema):
    keys = ("properties", "relationships", "dictionaries", "action_values", "done_by_basis", "semantics")
    return json.dumps({key: schema[key] for key in keys}, ensure_ascii=False, indent=2)


def make_llm():
    from langchain_openai import ChatOpenAI

    model = os.getenv("TEXT2CYPHER_MODEL") or os.getenv("SCHOOL_RERANK_MODEL") or "gpt-6-luna"
    extra = {"reasoning_effort": "none"} if model == "gpt-6-luna" else {}
    return ChatOpenAI(model=model, temperature=0, timeout=30, max_retries=1, **extra)


def generate_cypher(question, schema, llm):
    prompt = (
        "학교 그래프 개발자용 Text2Cypher. 아래 실제 구조·값 사전만 사용한다. "
        "설명·코드블록 없이 단일 읽기 Cypher만 출력한다. 지원 밖 대상이나 쓰기 요청이면 "
        "UNSUPPORTED: 사유 한 줄만 출력한다. 없는 ID를 만들지 않는다. "
        "그래프 요청은 MATCH path=... RETURN path처럼 Path/노드/관계 자체를 반환한다. "
        "집계는 표 속성으로 RETURN한다. 명시적 관계만 따라가며 서로 독립인 노드의 곱집합을 만들지 않는다. "
        "질병 관련 원문과 행동은 ABOUT 경로와 FOR_DISEASE 경로를 CALL { ... UNION ... }로 독립 조회한다. "
        "선정 질병의 COMMON 행동을 임의로 FOR_DISEASE 관계가 있다고 해석하지 않는다. "
        "행동 없는 청크도 행동과 함께 요청하면 OPTIONAL MATCH를 사용한다. 문서별 행동 수는 HAS_CHUNK와 "
        "SUPPORTED_BY로 연결하고 count(DISTINCT action)으로 센다. "
        "값 사전에서 질병 별칭과 ID, 개념명과 ID, 문서명과 ID를 찾는다. "
        "embedding/전체 properties()는 명시적으로 반환하지 않는다. 원문 문자열 반환은 left(text,200). "
        "Path에 든 속성은 표시할 때 제한한다. 외부 접근·프로시저·SHOW·쓰기·스키마 변경은 금지. "
        "CALL은 WITH 또는 범위 지정 인자로 전달하는 읽기 서브쿼리만 허용한다. "
        "탐색은 고정 길이 또는 최대 3홉, 정렬 후 최종 LIMIT 50 이하의 정수를 사용한다. "
        "UNION은 가능하면 CALL 서브쿼리 안에 두고 바깥 RETURN에 ORDER BY와 LIMIT를 둔다.\n"
        + build_schema_context(schema) + "\n예시:\n"
        + "\n".join(f"질문:{q}\nCypher:{c}" for q, c in EXAMPLES)
    )
    started = perf_counter()
    content = llm.invoke([("system", prompt), ("user", question)]).content
    if isinstance(content, list):
        content = "".join(p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text")
    if not isinstance(content, str):
        raise TypeError("LLM 응답 형식 오류")
    return {"question": question, "generated_cypher": content, "generation_seconds": perf_counter() - started}


FUNCTIONS = {
    "COUNT", "COLLECT", "DISTINCT", "SIZE", "LEFT", "RIGHT", "SUBSTRING", "COALESCE", "HEAD", "LAST",
    "MIN", "MAX", "SUM", "AVG", "ROUND", "TOLOWER", "TOUPPER", "TRIM", "ANY", "ALL", "NONE", "SINGLE",
    "LABELS", "TYPE", "NODES", "RELATIONSHIPS", "LENGTH", "STARTNODE", "ENDNODE", "EXISTS", "TOSTRING",
}


def validate_cypher(driver, database, generated, schema):
    result = {"status": "REJECTED", "cypher": generated, "query_type": None, "reason": "", "seconds": 0}
    started = perf_counter()
    try:
        if generated.strip().startswith("UNSUPPORTED:"):
            result.update(status="UNSUPPORTED", reason="모델이 지원 밖 또는 쓰기 요청으로 거부")
            return result
        cypher = _clean_cypher(generated)
        result["cypher"] = cypher
        code = _code_mask(cypher)
        if not re.match(r"\s*(MATCH|OPTIONAL\s+MATCH|WITH)\b", code, re.IGNORECASE):
            raise ValueError("MATCH/WITH 조회만 허용")
        if re.search(r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|ALTER|GRANT|DENY|REVOKE|LOAD|SHOW|USE|FOREACH|INSERT)\b", code, re.IGNORECASE):
            raise ValueError("쓰기·관리·외부 조회 구문 거부")
        for call in re.finditer(r"\bCALL\b", code, re.IGNORECASE):
            if not re.match(r"\s*(?:\([\w\s,]*\)\s*)?\{", code[call.end():]):
                raise ValueError("프로시저 호출 거부; CALL 읽기 서브쿼리만 허용")
        if re.search(r"\bembedding\b", cypher, re.IGNORECASE) or re.search(r"\bproperties\s*\(", code, re.IGNORECASE):
            raise ValueError("전체 속성·embedding 명시 반환 거부")
        relation_names = {pattern[1] for pattern in PATTERNS}
        for pattern in re.finditer(r"([\(\[])\s*(?:\w+\s*)?:\s*(\w+)", code):
            allowed = schema["properties"] if pattern[1] == "(" else relation_names
            if pattern[2] not in allowed:
                raise ValueError("관찰된 스키마에 없는 노드·관계")
        if re.search(r"\b[a-zA-Z_]\w*\.[a-zA-Z_]\w*\s*\(", code):
            raise ValueError("이름공간 함수·외부 기능 거부")
        # MATCH 패턴의 (node), CALL 인자와 Cypher 문법 괄호는 함수 호출이 아니다.
        for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", code):
            word = match[1].upper()
            if word not in FUNCTIONS | {"MATCH", "OPTIONAL", "WHERE", "WITH", "RETURN", "CALL", "IN", "NOT", "AND", "OR"}:
                raise ValueError("허용하지 않은 함수")
        for relationship in re.findall(r"\[[^\]]*\*[^\]]*\]", code):
            bounded = re.search(r"\*\s*\d*\s*\.\.\s*(\d+)", relationship)
            fixed = re.search(r"\*\s*(\d+)\s*\]", relationship)
            depth = int((bounded or fixed)[1]) if bounded or fixed else 0
            if not 1 <= depth <= 3:
                raise ValueError("무제한 또는 3홉 초과 경로 거부")
        explicit_limits = list(re.finditer(r"\bLIMIT\s+(\d+)\b", code, re.IGNORECASE))
        if not explicit_limits or any(int(m[1]) > MAX_ROWS for m in explicit_limits):
            raise ValueError("명시적 LIMIT 50 이하 필요; 쿼리 의미를 바꾸는 자동 덧붙이기는 하지 않음")
        if not re.search(r"\bLIMIT\s+\d+\s*$", code, re.IGNORECASE):
            raise ValueError("최종 LIMIT 필요")
        ids = {row["id"] for rows in schema["dictionaries"].values() for row in rows}
        literal_ids = re.findall(r"['\"]([A-Z][A-Z0-9_]+)['\"]", cypher)
        enums = {str(v) for row in schema["action_values"] for v in row.values() if v is not None}
        enums |= {str(row["basis"]) for row in schema["done_by_basis"]}
        if any(value not in ids | enums for value in literal_ids):
            result.update(status="UNSUPPORTED", reason="실제 사전에 없는 ID 또는 값")
            return result
        with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
            summary = session.run(Query("EXPLAIN " + cypher, timeout=10)).consume()
        result["query_type"] = summary.query_type
        if summary.query_type != "r":
            raise ValueError("EXPLAIN query_type이 r이 아님")

        def operators(plan):
            if not plan:
                return []
            return [plan.get("operatorType", "")] + [op for child in plan.get("children", []) for op in operators(child)]

        if any("CartesianProduct" in op for op in operators(summary.plan)):
            raise ValueError("곱집합 실행 계획 거부")
        result.update(status="VALID", reason="단일 조회·실행 범위·EXPLAIN r 확인")
    except ValueError as error:
        # 이 블록의 ValueError와 _clean_cypher 오류는 코드가 만든 고정 메시지다.
        result["reason"] = str(error)
    except Exception as error:  # noqa: BLE001 -- DB 오류 원문에 접속 정보가 섞일 수 있음
        result.update(status="SYNTAX_ERROR" if "Syntax" in str(getattr(error, "code", "")) else "VALIDATION_ERROR",
                      error=safe_error(error), reason="DB 검사 실패; 비밀 보호를 위해 오류 원문 생략")
    finally:
        result["seconds"] = perf_counter() - started
    return result


def execute_cypher(driver, database, validation, schema):
    """검증한 문자열을 그대로 한 번 실행하고 Record·Path를 보존한다."""
    if validation.get("status") != "VALID" or validation.get("query_type") != "r":
        raise ValueError("검증되지 않은 Cypher는 실행하지 않음")
    checked = validate_cypher(driver, database, validation["cypher"], schema)
    if checked["status"] != "VALID" or checked["cypher"] != validation["cypher"]:
        raise ValueError("실행 직전 재검사 실패 또는 쿼리 변경")
    query = Query(validation["cypher"], timeout=10)

    @unit_of_work(timeout=query.timeout)
    def read(tx):
        return list(islice(tx.run(query.text), MAX_ROWS + 1))

    started = perf_counter()
    with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        records = session.execute_read(read)
    return {"records": records[:MAX_ROWS], "truncated": len(records) > MAX_ROWS,
            "possibly_truncated": len(records) >= MAX_ROWS, "execution_seconds": perf_counter() - started,
            "executed_cypher": validation["cypher"]}


def stable_node(node):
    for label in sorted(node.labels):
        key = LABEL_KEYS.get(label)
        if key and node.get(key) is not None:
            return f"{label}:{node[key]}"
    raise ValueError("지원 스키마의 안정 ID가 없는 노드")


def safe_properties(value):
    return {key: (val[:200] if isinstance(val, str) else val) for key, val in dict(value).items()
            if key != "embedding"}


def serialize_records(records):
    nodes, edges = {}, {}

    def convert(value):
        if isinstance(value, Node):
            ident = stable_node(value)
            nodes[ident] = {"id": ident, "labels": sorted(value.labels), "properties": safe_properties(value)}
            return {"node_id": ident}
        if isinstance(value, Relationship):
            source, target = stable_node(value.start_node), stable_node(value.end_node)
            if (source.split(":", 1)[0], value.type, target.split(":", 1)[0]) not in PATTERNS:
                raise ValueError("허용 스키마 밖의 관계 또는 반대 방향")
            convert(value.start_node)
            convert(value.end_node)
            props = safe_properties(value)
            key = (source, value.type, target, json.dumps(props, sort_keys=True))
            edges[key] = {"source": source, "type": value.type, "target": target, "properties": props}
            return {"source": source, "type": value.type, "target": target}
        if isinstance(value, GraphPath):
            return {"nodes": [convert(n) for n in value.nodes], "relationships": [convert(r) for r in value.relationships]}
        if isinstance(value, (list, tuple)):
            if len(value) > MAX_ROWS and all(isinstance(v, (int, float)) for v in value):
                return {"omitted": "long numeric array", "length": len(value)}
            return [convert(v) for v in value]
        if isinstance(value, dict):
            return {str(k): convert(v) for k, v in value.items() if k != "embedding"}
        if isinstance(value, str):
            return value[:200]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:200]

    rows = [{key: convert(record[key]) for key in record.keys()} for record in records]  # noqa: SIM118 -- Record의 기본 순회는 값
    all_nodes, all_edges = list(nodes.values()), list(edges.values())
    display_nodes = all_nodes[:MAX_NODES]
    display_ids = {n["id"] for n in display_nodes}
    display_edges = [e for e in all_edges if e["source"] in display_ids and e["target"] in display_ids][:MAX_EDGES]
    return {"rows": rows, "graph": {"nodes": display_nodes, "relationships": display_edges,
            "node_count": len(all_nodes), "relationship_count": len(all_edges),
            "truncated": len(display_nodes) < len(all_nodes) or len(display_edges) < len(all_edges)}}


def prepare_browser_query(validation):
    if validation.get("status") != "VALID":
        raise ValueError("검증을 통과한 Cypher만 복사 가능")
    return validation["cypher"]


def identity_sets(serialized):
    graph = serialized["graph"]
    return {"nodes": sorted(n["id"] for n in graph["nodes"]),
            "edges": sorted((e["source"], e["type"], e["target"], json.dumps(e["properties"], sort_keys=True))
                            for e in graph["relationships"])}
