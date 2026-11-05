"""추출 모듈(가짜 LLM)·여러 실행 폴더 합치기·검수 개념 선택·검수 전 점검 검사.

LLM·Neo4j 없이 실행한다. 픽스처 문구는 인공 예시다.
"""

import copy
import json
from pathlib import Path
from typing import ClassVar

import pytest

from index.school.bundle import build_load_bundle
from index.school.id_map import IdRegistry, attach_official_ids, document_node
from index.school.normalize import (
    ArtifactError,
    candidate_key,
    load_artifacts,
    load_runs,
    normalize_candidates,
    precheck,
    select_approved,
    select_approved_runs,
)
from preprocess.school.extract import (
    ActionCandidate,
    ActionCandidates,
    RoleCandidate,
    annotate_candidates,
    extract_chunk,
    save_run,
)
from preprocess.school.review_web import candidate_key as web_candidate_key
from tests.rag.school.test_school_normalize import approve, make_candidate, world  # noqa: F401

CONCEPT_DRAFT = "`CONTACT_HEALTH_TEACHER` `VENTILATE_DISINFECT`"


class FakeRaw:
    usage_metadata: ClassVar[dict] = {"total_tokens": 10}


class FakeModel:
    """with_structured_output(...).invoke(prompt)만 흉내 낸다."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def with_structured_output(self, schema, include_raw):
        assert schema is ActionCandidates and include_raw
        return self

    def invoke(self, prompt):
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def parsed(*evidence, role_evidence="담임교사"):
    return {"raw": FakeRaw(), "parsed": ActionCandidates(candidates=[
        ActionCandidate(action_text=f"[인공] {e}", scope="COMMON", stage="RESPONSE_1", step_order=1,
                        modality="UNSPECIFIED", concept_candidate="VENTILATE_DISINFECT", evidence=e,
                        roles=[RoleCandidate(role_text="담임교사", basis="EXAMPLE",
                                             basis_evidence=role_evidence)])
        for e in evidence])}


# ---------------------------------------------------------------- 추출

def test_extract_chunk_success_records_candidates(world):  # noqa: F811
    chunk = world["artifacts"]["chunks"][0]
    model = FakeModel([parsed("교실 환기")])
    record, rows = extract_chunk(chunk, CONCEPT_DRAFT, model, "fake")
    assert record["status"] == "SUCCESS" and record["token_usage"] == {"total_tokens": 10}
    assert rows[0]["review_status"] == "PENDING" and rows[0]["source_chunk_id"] == chunk["chunk_id"]
    assert "[row]" in model.prompts[0] and "편집자 참고 메모: 원문 인용 아님" in model.prompts[0]


def test_extract_chunk_retries_then_records_failure(world):  # noqa: F811
    chunk = world["artifacts"]["chunks"][0]
    record, rows = extract_chunk(chunk, CONCEPT_DRAFT,
                                 FakeModel([TimeoutError(), {"raw": None, "parsed": None,
                                                             "parsing_error": ValueError()}]), "fake")
    assert (record["status"], record["error_type"], record["retry_count"], rows) == \
        ("PARSE_FAILED", "ValueError", 1, [])
    record, _ = extract_chunk(chunk, CONCEPT_DRAFT, FakeModel([TimeoutError(), parsed("교실 환기")]),
                              "fake")
    assert record["status"] == "SUCCESS" and record["retry_count"] == 1


def test_quote_check_pass_means_quotes_found(world):  # noqa: F811
    chunk = world["artifacts"]["chunks"][0]
    _, rows = extract_chunk(chunk, CONCEPT_DRAFT, FakeModel([parsed("교실 환기", "없는 문장")]), "fake")
    rows = annotate_candidates(rows, {chunk["chunk_id"]: chunk}, CONCEPT_DRAFT)
    assert [r["quote_check_pass"] for r in rows] == [True, False]
    # 예시 역할 사유는 남지만(검수 항목으로 해소) 인용 확인 결과와 섞지 않는다.
    assert "예시 단서와 역할 배정의 관계를 PDF에서 확인 필요" in rows[0]["check_reasons"]


def test_saved_run_is_pending_and_linked_by_fingerprint(world, tmp_path):  # noqa: F811
    chunks = world["artifacts"]["chunks"]
    runs, rows = [], []
    for chunk, response in ((chunks[0], parsed("교실 환기")), (chunks[1], TimeoutError())):
        record, found = extract_chunk(chunk, CONCEPT_DRAFT, FakeModel([response, response]), "fake")
        runs.append(record)
        rows += found
    folder = save_run(tmp_path, chunks, annotate_candidates(rows, {c["chunk_id"]: c for c in chunks},
                                                            CONCEPT_DRAFT), runs)
    artifacts = load_artifacts(folder)          # 한 청크 호출 실패가 있어도 성공분은 읽는다
    assert [r["status"] for r in artifacts["failed_runs"]] == ["CALL_FAILED"]
    assert json.loads((folder / "run.jsonl").read_text(encoding="utf-8").splitlines()[0])["status"] \
        == "SUCCESS"                            # 검수 화면은 첫 행이 SUCCESS인 폴더만 보여 줌
    ready, held = select_approved(artifacts)
    assert not ready and held[0]["hold_reasons"][0] == "미승인: PENDING"
    assert artifacts["reviews"][0]["candidate_fingerprint"] == candidate_key(artifacts["candidates"][0])


def test_web_review_fingerprint_matches_normalizer():
    candidate = {"b": 1, "a": "한글", "roles": []}
    assert web_candidate_key(candidate) == candidate_key(candidate)


# ---------------------------------------------------------------- 여러 실행 폴더

def _write_run(folder: Path, artifacts):
    folder.mkdir()
    for name, rows in (("chunks", artifacts["chunks"]), ("steps_raw", artifacts["candidates"]),
                       ("review", artifacts["reviews"]),
                       ("run", [{"status": "SUCCESS", "source_chunk_id": c["chunk_id"]}
                                for c in artifacts["chunks"]])):
        (folder / f"{name}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return folder


def test_load_runs_merges_identical_chunks(world, tmp_path):  # noqa: F811
    first = copy.deepcopy(world["artifacts"])
    second = copy.deepcopy(world["artifacts"])
    first["candidates"], first["reviews"] = first["candidates"][:2], first["reviews"][:2]
    second["candidates"], second["reviews"] = second["candidates"][2:], second["reviews"][2:]
    loaded, chunks = load_runs([_write_run(tmp_path / "a", first), _write_run(tmp_path / "b", second)])
    ready, held = select_approved_runs(loaded)
    assert len(chunks) == 3 and len(ready) == 4 and not held
    assert {r["run_dir"] for r in ready} == {"a", "b"}


def test_load_runs_rejects_changed_chunk(world, tmp_path):  # noqa: F811
    other = copy.deepcopy(world["artifacts"])
    other["chunks"][0]["text"] += " "
    with pytest.raises(ArtifactError, match="내용이 다른 청크"):
        load_runs([_write_run(tmp_path / "a", world["artifacts"]), _write_run(tmp_path / "b", other)])


def test_candidate_from_failed_chunk_is_rejected(world, tmp_path):  # noqa: F811
    folder = _write_run(tmp_path / "a", world["artifacts"])
    (folder / "run.jsonl").write_text(json.dumps({"status": "SUCCESS", "source_chunk_id": "x"}) + "\n",
                                      encoding="utf-8")
    with pytest.raises(ArtifactError, match="성공 기록이 없는 청크"):
        load_artifacts(folder)


# ---------------------------------------------------------------- 검수 개념 선택

def _pipeline(world):  # noqa: F811
    ready, held = select_approved(world["artifacts"])
    normalized, held2, _ = normalize_candidates(ready, world["artifacts"]["chunks"], world["raw"],
                                                world["books"])
    actions, _, official = attach_official_ids(normalized, world["artifacts"]["chunks"], world["raw"],
                                               world["manifest"], IdRegistry(path=Path("unused.json")))
    docs = [document_node(d, world["manifest"]) for d in {o["document_id"] for o in official.values()}]
    return actions, held + held2, build_load_bundle(actions, list(official.values()), docs,
                                                    world["books"])


def test_reviewer_concept_overrides_llm_candidate(world):  # noqa: F811
    # 3번 후보는 LLM이 NEW_CONCEPT_CANDIDATE. 검수자가 표준 개념을 고르면 적재된다.
    world["artifacts"]["reviews"][2]["edited_concept_id"] = "ESCORT_TO_HEALTH_ROOM"
    actions, held, bundle = _pipeline(world)
    chosen = next(a for a in actions if a["candidate_index"] == 3)
    assert chosen["concept"] == {"id": "ESCORT_TO_HEALTH_ROOM", "status": "RESOLVED", "reason": "",
                                 "source": "REVIEW"}
    assert not held and not bundle["deferred"] and len(bundle["nodes"]["SchoolAction"]) == 4


def test_pending_concept_is_deferred_and_unknown_is_held(world):  # noqa: F811
    world["artifacts"]["reviews"][2]["edited_concept_id"] = "TEMPORARY_OBSERVATION"   # 승인 대기
    world["artifacts"]["reviews"][0]["edited_concept_id"] = "MADE_UP"                  # 사전 밖
    actions, held, bundle = _pipeline(world)
    pending = next(a for a in actions if a["candidate_index"] == 3)
    assert pending["concept"]["status"] == "PENDING_CONCEPT"
    # 기본 정책(omit_link): 행동은 적재하고 개념 연결만 생략
    assert pending["action_id"] in {a["action_id"] for a in bundle["nodes"]["SchoolAction"]}
    assert not any(e["start_id"] == pending["action_id"] for e in bundle["edges"]
                   if e["type"] == "INSTANCE_OF")
    assert not bundle["deferred"] and bundle["warnings"]
    assert any("사전 밖 개념 ID: MADE_UP" in r for h in held for r in h["hold_reasons"])


# ---------------------------------------------------------------- 검수 전 점검

def test_role_evidence_from_title_is_accepted_but_editorial_memo_is_not(world):  # noqa: F811
    # 예시 단서가 표 제목에 있는 역할표: 제목 인용은 원문 조각이므로 받아들인다.
    candidate = world["artifacts"]["candidates"][1]
    candidate["roles"][0]["basis_evidence"] = "인공 시나리오: 대응 1단계 주요 조치사항"
    rows = precheck(world["artifacts"], world["raw"], world["books"])
    assert not rows[1]["auto_reasons"]
    # 편집 메모에만 있는 문장 + 행 담당과 다른 표기 → 근거 없음
    candidate["roles"][0].update(role_text="보건교사", basis_evidence="편집 메모: 실제 역할은 예시입니다.")
    assert any("역할 근거가 원문 조각" in r for r in precheck(world["artifacts"], world["raw"],
                                                           world["books"])[1]["auto_reasons"])
    # 같은 표기가 행 담당과 같으면 근거 위치와 무관하게 행 구조가 근거
    candidate["roles"][0].update(role_text="담임교사")
    assert not precheck(world["artifacts"], world["raw"], world["books"])[1]["auto_reasons"]
    rows = precheck(world["artifacts"], world["raw"], world["books"])
    assert rows[0]["concept"] == "VENTILATE_DISINFECT" and rows[2]["concept"] == "NEW_CONCEPT"
