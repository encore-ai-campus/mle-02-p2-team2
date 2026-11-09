"""자연어 질문으로 Cypher 한 개를 생성하고 EXPLAIN 검사 후 읽기만 한다."""

from __future__ import annotations

import argparse
import json
import re
from itertools import islice

from neo4j import READ_ACCESS, Query, unit_of_work

from index.school.neo4j_local import get_driver
from rag.respiratory.graph import PROJECT_ROOT

FIXTURE_DIR = PROJECT_ROOT / "tests/fixtures/respiratory"
MAX_ROWS = 50
CITATION_WARNING = "Text2Cypher 결과는 근거 인용(Citation)을 대신하지 않음"
SCHEMA = """노드:
Document(document_id,title,issuer,version,region)
Chunk(chunk_id,text,pdf_page,printed_page,section)
SchoolAction(action_id,action_text,scope,stage,step_order,modality)
Disease(disease_id,name,aliases)
SchoolRole(role_id,name,aliases)
ActionConcept(concept_id,name,group)
관계(방향 고정):
Document-[:HAS_CHUNK]->Chunk
Chunk-[:ABOUT]->Disease
SchoolAction-[:SUPPORTED_BY]->Chunk
SchoolAction-[:FOR_DISEASE]->Disease
SchoolAction-[:DONE_BY{basis}]->SchoolRole
SchoolAction-[:INSTANCE_OF]->ActionConcept
"""
EXAMPLES = (
    (
        "문서별 청크 수는?",
        ("MATCH (doc:Document) OPTIONAL MATCH (doc)-[:HAS_CHUNK]->(chunk:Chunk) "
         "RETURN doc.document_id AS document_id, doc.title AS title, "
         "count(DISTINCT chunk) AS chunk_count ORDER BY document_id LIMIT 50"),
    ),
    (
        "성홍열 관련 학교 행동과 근거 문서는?",
        ("MATCH (action:SchoolAction)-[:FOR_DISEASE]->"
        "(disease:Disease {disease_id: 'SCARLET_FEVER'}) "
        "MATCH (doc:Document)-[:HAS_CHUNK]->(chunk:Chunk)<-[:SUPPORTED_BY]-(action) "
        "RETURN doc.document_id AS document_id, chunk.pdf_page AS pdf_page, "
        "action.action_id AS action_id, action.action_text AS action_text "
         "ORDER BY document_id, action_id LIMIT 50"),
    ),
    (
        "보호자 연락 행동이 있는 문서는?",
        ("MATCH (doc:Document)-[:HAS_CHUNK]->(chunk:Chunk)<-[:SUPPORTED_BY]-"
        "(action:SchoolAction)-[:INSTANCE_OF]->"
        "(concept:ActionConcept {concept_id: 'CONTACT_GUARDIAN'}) "
        "RETURN DISTINCT doc.document_id AS document_id, doc.title AS title, "
        "action.action_id AS action_id, action.action_text AS action_text, "
         "chunk.pdf_page AS pdf_page ORDER BY document_id, action_id LIMIT 50"),
    ),
)
PROMPT = (
    "아래 스키마만 사용하는 Neo4j Cypher 한 개를 생성한다. "
    "설명이나 코드블록 없이 Cypher만 출력한다. 읽기 문법만 사용한다. "
    "스키마에 없는 노드·관계·속성이나 외부 자원을 사용하지 않는다. "
    "embedding 속성을 RETURN하지 않는다. 노드·관계·경로 전체 또는 properties() 대신 "
    "필요한 속성을 명시적으로 RETURN한다. 질병은 disease_id 또는 name·aliases로 찾는다. "
    "Chunk.text를 RETURN할 때는 반드시 left(chunk.text,200)처럼 앞 200자만 반환한다. "
    "LIMIT은 50 이하의 정수로 쓴다.\n\n"
    + SCHEMA
    + "\n예시:\n"
    + "\n\n".join(f"질문: {question}\nCypher: {cypher}" for question, cypher in EXAMPLES)
)


class NotReadOnlyError(ValueError):
    """생성 결과가 단일 조회문으로 취급될 수 없음."""


def _default_deps() -> dict:
    driver, database = get_driver(PROJECT_ROOT / ".env")
    try:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model="gpt-6-luna", temperature=0, timeout=30,
            max_retries=1, reasoning_effort="none",
        )
    except Exception:
        driver.close()
        raise
    return {"driver": driver, "database": database, "llm": llm}


def _code_mask(cypher: str) -> str:
    """문자열·식별자·주석을 가려 세미콜론과 LIMIT의 실제 위치만 찾는다."""
    pattern = r"'(?:\\.|''|[^'\\])*'|\"(?:\\.|\"\"|[^\"\\])*\"|`(?:``|[^`])*`|//[^\n]*|/\*[\s\S]*?\*/"
    return re.sub(pattern, lambda match: " " * len(match[0]), cypher)


def _clean_cypher(cypher: str) -> str:
    cypher = cypher.strip()
    if cypher.startswith("```"):
        fenced = re.fullmatch(r"```(?:cypher)?\s*([\s\S]*?)\s*```", cypher, re.IGNORECASE)
        if not fenced:
            raise NotReadOnlyError("Cypher 코드블록 형식 오류")
        cypher = fenced[1].strip()
    if not cypher or "```" in _code_mask(cypher):
        raise NotReadOnlyError("단일 Cypher가 필요함")
    masked = _code_mask(cypher)
    separators = [match.start() for match in re.finditer(";", masked)]
    if len(separators) > 1 or (separators and masked[separators[0] + 1:].strip()):
        raise NotReadOnlyError("여러 Cypher 문장은 실행하지 않음")
    if separators:
        index = separators[0]
        cypher = (cypher[:index] + cypher[index + 1:]).rstrip()
    if not _code_mask(cypher).strip():
        raise NotReadOnlyError("빈 Cypher는 실행하지 않음")
    return cypher


def _ensure_limit(cypher: str) -> str:
    masked = _code_mask(cypher)
    limits = list(re.finditer(r"\bLIMIT\b", masked, re.IGNORECASE))
    if not limits:
        return cypher.rstrip() + "\nLIMIT 50"
    for token in reversed(limits):
        value = re.match(r"\s+(\d+)\b", masked[token.end():])
        if not value:
            raise NotReadOnlyError("LIMIT에는 정수 상수만 허용함")
        start, end = token.end() + value.start(1), token.end() + value.end(1)
        if not re.match(r"\s*(?:$|\}|UNION\b)", masked[end:], re.IGNORECASE):
            raise NotReadOnlyError("LIMIT에는 정수 상수만 허용함")
        if int(value[1]) > MAX_ROWS:
            cypher = cypher[:start] + str(MAX_ROWS) + cypher[end:]
    return cypher


def _is_read_only(session, cypher: str) -> bool:
    return session.run("EXPLAIN " + cypher).consume().query_type == "r"


def _execute_rows(session, cypher: str) -> list[dict]:
    query = Query(cypher, timeout=10)

    @unit_of_work(timeout=query.timeout)
    def read(tx):
        # 설치된 드라이버는 managed tx.run(Query)를 거부하므로 timeout은 트랜잭션에 전달한다.
        return [record.data() for record in islice(tx.run(query.text), MAX_ROWS)]

    return session.execute_read(read)


def text2cypher_search(question: str, deps=None) -> dict:
    result = {
        "tool_name": "text2cypher_search", "status": "ERROR", "question": question,
        "cypher": "", "rows": [], "row_count": 0, "warnings": [CITATION_WARNING],
    }
    owned_driver = None
    try:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("질문이 비어 있음")
        if deps is None:
            deps = _default_deps()
            owned_driver = deps["driver"]
        response = deps["llm"].invoke([("system", PROMPT), ("user", question)])
        content = response.content
        if isinstance(content, list):
            content = "".join(part["text"] for part in content
                              if isinstance(part, dict) and part.get("type") == "text")
        if not isinstance(content, str):
            raise TypeError("LLM 응답이 문자열이 아님")
        result["cypher"] = content
        result["cypher"] = _ensure_limit(_clean_cypher(content))
        with deps["driver"].session(
            database=deps.get("database"), default_access_mode=READ_ACCESS,
        ) as session:
            if not _is_read_only(session, result["cypher"]):
                result["status"] = "NOT_READ_ONLY"
                result["warnings"].append("EXPLAIN query_type이 r이 아니므로 실행하지 않음")
                return result
            result["rows"] = _execute_rows(session, result["cypher"])
        result["row_count"] = len(result["rows"])
        result["status"] = "OK" if result["rows"] else "NO_RESULT"
    except NotReadOnlyError as error:
        result["status"] = "NOT_READ_ONLY"
        result["warnings"].append(str(error))
    except Exception as error:  # noqa: BLE001
        # DB/API 오류 원문에는 접속 정보가 섞일 수 있으므로 종류만 남긴다.
        result["warnings"].append(type(error).__name__)
    finally:
        if owned_driver is not None:
            try:
                owned_driver.close()
            except Exception as error:  # noqa: BLE001
                result["warnings"].append(type(error).__name__)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM Text2Cypher 읽기 전용 조회")
    parser.add_argument("question", help="자연어 조회 질문")
    parser.add_argument("--save", help="결과 fixture 이름")
    args = parser.parse_args()
    if args.save and not re.fullmatch(r"[A-Za-z0-9_-]+", args.save):
        parser.error("--save 이름에는 영문·숫자·하이픈·밑줄만 사용할 수 있습니다.")
    result = text2cypher_search(args.question)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.save:
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        (FIXTURE_DIR / f"{args.save}_t2c_result.json").write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
