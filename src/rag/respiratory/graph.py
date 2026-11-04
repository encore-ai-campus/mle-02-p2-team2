"""RespiratoryGraph: 호흡기 문서의 원문과 학교 행동을 근거로 반환한다.

LangGraph가 범위 확인 → 청크 검색 → 행동 조회 → 결과 조립 순서로 동작한다.
LLM으로 답을 생성하지 않으며, 검색과 조립에 사용한 원문·ID를 Citation에 남긴다.
질문 임베딩은 팀의 공통 임베딩 모듈을 사용한다.

실행 위치: 저장소 루트
실행 예시: uv run python -m rag.respiratory.graph "성홍열 학교 대응은?" --save R-Q2
"""

import argparse
import json
import re
from pathlib import Path
from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from index.school.dictionaries import compare_key, load_dictionaries
from index.school.embed import make_embedder
from index.school.neo4j_local import get_driver
from rag.school.contract_local import (
    QueryInput,
    QueryOutput,
    empty_output,
    validate_query_input,
    validate_query_output,
)
from rag.school.search import SearchError, expand_school_actions, search_school_chunks

DOMAIN = "respiratory"
OUR_DOCS = (
    "kdca_respiratory_2026",
    "kdca_flu_facility_2026",
    "kdca_covid19_2026",
)
MODALITY_LABELS = {
    "MANDATORY": "의무",
    "RECOMMENDED": "권장",
    "DISCRETIONARY": "선택",
}
PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = PROJECT_ROOT / "tests/fixtures/respiratory"


class RespiratoryState(TypedDict, total=False):
    """공통 입력·출력 외에 각 단계가 전달할 값을 보관한다."""

    question: str
    region: str
    allowed_document_ids: list[str]
    task: NotRequired[str]
    preferred_document_ids: NotRequired[list[str]]
    context_entity_ids: NotRequired[list[str]]
    request: dict
    allowed: list[str]
    detected_diseases: list[str]
    hits: list[dict]
    actions: list[dict]
    domain: str
    status: str
    findings: list[dict]
    citations: list[dict]
    action_ids: list[str]
    warnings: list[str]
    answer: str


def trace_step(deps, step, **details):
    """검색 과정은 호출자가 제공한 trace 목록에만 기록한다."""
    trace = deps.get("trace")
    if isinstance(trace, list):
        trace.append({"step": step, **details})


def detect_diseases(question, books):
    """질문의 질병명·별칭을 사전과 비교해 명시된 질병 ID만 찾는다.

    비교할 때만 공백과 표기를 정리한다. 질문 원문과 사전의 표기는 바꾸지 않는다.
    """
    question_key = compare_key(question)
    found = []
    for entry in books["disease"]["entries"]:
        names = [entry["name"], *entry.get("aliases", [])]
        if any(compare_key(name) and compare_key(name) in question_key for name in names):
            disease_id = entry["disease_id"]
            if disease_id not in found:
                found.append(disease_id)
    return found


def excerpt_for_hit(hit, actions):
    """청크에서 관련 행동이 처음 나오는 지점 주변을 최대 200자로 자른다.

    같은 청크에 여러 행동이 있으면 원문에서 가장 앞에 나오는 행동을 기준으로 한다.
    행동 문구를 찾지 못하면 청크의 앞부분을 쓴다. 출력 시 줄바꿈만 공백으로 바꾼다.
    """
    text = hit["text"].replace("\r", " ").replace("\n", " ")
    positions = []
    for action in actions:
        action_text = action["action_text"].replace("\r", " ").replace("\n", " ")
        if action_text:
            position = text.find(action_text)
            if position >= 0:
                positions.append(position)
    start = max(0, min(positions) - 80) if positions else 0
    return text[start:start + 200]


def make_citation(hit, actions):
    """검색 결과의 문서·쪽수를 유지해 청크 하나의 Citation을 만든다."""
    return {
        "document_id": hit["document_id"],
        "chunk_id": hit["chunk_id"],
        "pdf_page": hit["pdf_page"],
        "printed_page": hit["printed_page"],
        "excerpt": excerpt_for_hit(hit, actions),
    }


def compose(state):
    """검색된 행동 또는 원문 청크로 Finding과 Citation을 조립한다."""
    hits = state["hits"]
    actions = state["actions"]
    findings = []
    citations = []
    action_ids = []
    warnings = []

    if actions:
        hits_by_id = {hit["chunk_id"]: hit for hit in hits}
        actions_by_chunk = {}
        for action in actions:
            # 검색한 청크만 Citation의 근거로 쓴다. 없는 ID는 오류로 처리한다.
            if action["chunk_id"] not in hits_by_id:
                raise ValueError("행동의 근거 청크가 검색 결과에 없습니다.")
            actions_by_chunk.setdefault(action["chunk_id"], []).append(action)
            claim = action["action_text"]
            if action.get("when_text"):
                claim = f"(조건:{action['when_text']}) {claim}"
            label = MODALITY_LABELS.get(action.get("modality"))
            if label:
                claim = f"{claim} [{label}]"
            findings.append({
                "claim": claim,
                "chunk_ids": [action["chunk_id"]],
                "action_ids": [action["action_id"]],
                "disease_ids": action["disease_ids"],
                "concept_ids": action["concept_ids"],
            })
            if action["action_id"] not in action_ids:
                action_ids.append(action["action_id"])

        # 여러 행동이 한 청크를 사용해도 Citation은 그 청크당 하나만 반환한다.
        for chunk_id, chunk_actions in actions_by_chunk.items():
            citations.append(make_citation(hits_by_id[chunk_id], chunk_actions))
        status = "ANSWERED"
    else:
        # 구조화된 행동이 없어도 검색된 원문의 위치는 보여준다.
        # 원문이 있다는 사실과 질문에 대한 답을 확인했다는 사실은 구분한다.
        for hit in hits[:3]:
            findings.append({
                "claim": f"관련 원문:{hit.get('section') or ''}",
                "chunk_ids": [hit["chunk_id"]],
                "action_ids": [],
                "disease_ids": state["detected_diseases"],
                "concept_ids": [],
            })
            citations.append(make_citation(hit, []))
        status = "PARTIAL"
        warnings.append("구조화된 행동 없음,원문만 제시")

    return {
        "domain": DOMAIN,
        "status": status,
        "findings": findings,
        "citations": citations,
        "action_ids": action_ids,
        "warnings": warnings,
        "answer": "\n".join(f"- {finding['claim']}" for finding in findings),
    }


def build_graph(deps):
    """공통 계약을 입출력으로 쓰는 고정 순서 LangGraph를 만든다."""
    driver = deps["driver"]
    database = deps.get("database")
    embedder = deps.get("embedder")
    books = deps["books"]

    def guard(state):
        # 입력에는 세 Graph의 문서가 섞일 수 있으므로 호흡기 문서만 선택한다.
        allowed = [doc for doc in state["allowed_document_ids"] if doc in OUR_DOCS]
        request = {
            key: state[key]
            for key in QueryInput.__annotations__
            if key in state
        }
        diseases = detect_diseases(state["question"], books)
        trace_step(deps, "guard", allowed=allowed, disease_ids=diseases)
        if not allowed:
            return empty_output(DOMAIN, "INSUFFICIENT_EVIDENCE", "호흡기 담당 문서가 허용 목록에 없음")
        return {"request": request, "allowed": allowed, "detected_diseases": diseases}

    def search(state):
        request = {**state["request"], "allowed_document_ids": state["allowed"]}
        try:
            found = search_school_chunks(
                driver, request, books, embedder=embedder, mode="hybrid", k=8, database=database
            )
        except SearchError as error:
            # 예외 원문에는 접속 정보가 섞일 수 있으므로 종류만 남긴다.
            trace_step(deps, "search", error=type(error).__name__)
            return empty_output(DOMAIN, "ERROR", "RespiratoryGraph 실행 실패:SearchError")
        hits = found["hits"]
        trace_step(
            deps, "search", terms=found["terms"],
            chunk_ids=[hit["chunk_id"] for hit in hits], count=len(hits),
        )
        if not hits:
            return empty_output(DOMAIN, "INSUFFICIENT_EVIDENCE", "관련 원문 청크를 찾지 못함")
        return {"hits": hits}

    def expand(state):
        chunk_ids = [hit["chunk_id"] for hit in state["hits"]]
        try:
            actions = expand_school_actions(driver, chunk_ids, state["allowed"], database=database)
        except SearchError as error:
            trace_step(deps, "expand", error=type(error).__name__)
            return empty_output(DOMAIN, "ERROR", "RespiratoryGraph 실행 실패:SearchError")
        if state["detected_diseases"]:
            # 질문에 질병이 명시되면 해당 질병과 연결된 행동만 사용한다.
            # 공통 행동의 적용 여부는 질병 연결만으로 추측하지 않는다.
            diseases = set(state["detected_diseases"])
            actions = [
                action for action in actions
                if diseases.intersection(action["disease_ids"])
            ]
        trace_step(deps, "expand", count=len(actions), chunk_ids=chunk_ids)
        return {"actions": actions}

    def compose_node(state):
        result = compose(state)
        trace_step(deps, "compose", count=len(result["findings"]))
        return result

    def next_or_end(state):
        # 앞 단계가 근거 부족·오류 결과를 만들었다면 이후 단계를 실행하지 않는다.
        return "end" if state.get("status") else "next"

    graph = StateGraph(RespiratoryState, input_schema=QueryInput, output_schema=QueryOutput)
    graph.add_node("guard", guard)
    graph.add_node("search", search)
    graph.add_node("expand", expand)
    graph.add_node("compose", compose_node)
    graph.add_edge(START, "guard")
    graph.add_conditional_edges("guard", next_or_end, {"end": END, "next": "search"})
    graph.add_conditional_edges("search", next_or_end, {"end": END, "next": "expand"})
    graph.add_conditional_edges("expand", next_or_end, {"end": END, "next": "compose"})
    graph.add_edge("compose", END)
    return graph.compile()


def run(query: QueryInput, *, deps=None) -> QueryOutput:
    """입력 검사 후 그래프를 실행하고, 반환 전에 공통 출력 계약을 확인한다."""
    validate_query_input(query)
    owned_driver = None
    try:
        if deps is None:
            # 직접 생성한 드라이버만 이 함수에서 닫는다.
            # 외부에서 주입한 드라이버의 수명은 호출자가 관리한다.
            owned_driver, database = get_driver(PROJECT_ROOT / ".env")
            deps = {
                "driver": owned_driver,
                "database": database,
                "embedder": make_embedder(),
                "books": load_dictionaries(),
            }
        result = build_graph(deps).invoke(dict(query))
        validate_query_output(result, query)
    except Exception as error:  # noqa: BLE001 - 실행 오류를 계약된 ERROR 결과로 반환한다.
        if deps is not None:
            trace_step(deps, "run", error=type(error).__name__)
        result = empty_output(DOMAIN, "ERROR", f"RespiratoryGraph 실행 실패:{type(error).__name__}")
    finally:
        if owned_driver is not None:
            try:
                owned_driver.close()
            except Exception as error:  # noqa: BLE001 - 종료 오류도 민감한 원문 없이 표시한다.
                result = empty_output(
                    DOMAIN, "ERROR", f"RespiratoryGraph 실행 실패:{type(error).__name__}"
                )
    validate_query_output(result, query)
    return result


def main():
    """질문 하나를 실행하고 요청한 경우 입출력 JSON을 지정된 폴더에 저장한다."""
    parser = argparse.ArgumentParser(description="호흡기 감염병 지침 검색")
    parser.add_argument("question", help="호흡기 지침에 대해 확인할 질문")
    parser.add_argument("--save", help="입출력 fixture 이름(예: R-Q2)")
    args = parser.parse_args()
    if args.save and not re.fullmatch(r"[A-Za-z0-9_-]+", args.save):
        parser.error("--save 이름에는 영문·숫자·하이픈·밑줄만 사용할 수 있습니다.")

    query: QueryInput = {
        "question": args.question,
        "region": "NATIONAL",
        "allowed_document_ids": list(OUR_DOCS),
    }
    result = run(query)
    if args.save:
        # 경로 문자를 허용하지 않아 fixture 폴더 바깥에 저장되지 않게 한다.
        for suffix, data in (("input", query), ("output", result)):
            path = FIXTURE_DIR / f"{args.save}_{suffix}.json"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
