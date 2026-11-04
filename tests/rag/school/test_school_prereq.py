"""school 사전 준비물(임시 사전·ID 규약·계약 임시본·manifest) 검사.

Neo4j·LLM 없이 실행한다. 실제 PDF 대조와 의미 검수를 대신하지 않는다.
"""

import copy
import csv
from pathlib import Path

import pytest

from index.school.dictionaries import (
    DictionaryError,
    alias_index,
    compare_key,
    load_dictionaries,
    neo4j_rows,
    validate_dictionaries,
)
from index.school.ids_local import (
    IdError,
    action_id,
    chunk_block_id,
    chunk_row_id,
    parse_id,
    validate_context_entity_id,
)
from rag.school.contract_local import (
    ContractError,
    empty_output,
    validate_query_input,
    validate_query_output,
)

ROOT = Path(__file__).resolve().parents[3]
SCHOOL_DOCS = {"moe_school_manual_v3", "gyeonggi_school_guide_2025"}


# ---------- 임시 사전 ----------

@pytest.fixture(scope="module")
def books():
    return load_dictionaries()


def test_dictionary_counts(books):
    assert {kind: len(book["entries"]) for kind, book in books.items()} == {
        "disease": 11, "role": 11, "concept": 27}


def test_role_aliases_resolve_spacing_variants(books):
    index = alias_index(books["role"]["entries"], "role_id")
    assert index[compare_key("보건 (담당) 교사")] == "HEALTH_TEACHER"
    assert index[compare_key("담임 교사")] == "HOMEROOM_TEACHER"
    assert index[compare_key("행 정 실")] == "ADMIN_OFFICE"
    assert index[compare_key("발생감시팀")] == "INFECTION_MGMT_TEAM"


def test_compound_and_excluded_mentions_are_not_auto_linked(books):
    index = alias_index(books["role"]["entries"], "role_id")
    for mention in ("교장 및 교감", "담임(교과담당)교사", "보건소", "교육청", "보호자", "생활부장"):
        assert compare_key(mention) not in index


def test_review_aliases_are_not_auto_linked(books):
    index = alias_index(books["disease"]["entries"], "disease_id")
    assert compare_key("눈병") not in index
    assert index[compare_key("볼거리")] == "MUMPS"


def test_reporting_concepts_stay_separate(books):
    ids = {entry["concept_id"] for entry in books["concept"]["entries"]}
    assert {"REPORT_EDU_OFFICE", "NOTIFY_HEALTH_CENTER_LEGAL", "COOPERATE_HEALTH_AUTHORITY",
            "INFORM_GUARDIANS_NOTICE", "CONTACT_GUARDIAN"} <= ids
    pending = {item["proposed_id"] for item in books["concept"]["pending_candidates"]}
    assert not (pending & ids)


def test_alias_collision_is_detected(books):
    broken = copy.deepcopy(books)
    broken["role"]["entries"][0]["aliases"].append("담임")  # PRINCIPAL에 담임 별칭 → 충돌
    assert any("별칭 충돌" in problem for problem in validate_dictionaries(broken))
    with pytest.raises(DictionaryError):
        alias_index(broken["role"]["entries"], "role_id")


def test_neo4j_rows_keep_only_ontology_properties(books):
    assert set(neo4j_rows("concept", books)[0]) == {"concept_id", "name"}
    assert set(neo4j_rows("role", books)[0]) == {"role_id", "name", "aliases"}


# ---------- ID 규약 ----------

def test_id_builders_match_ontology_examples():
    assert chunk_block_id("moe_school_manual_v3", 45, 2) == "moe_school_manual_v3:p0045:b02"
    assert chunk_row_id("gyeonggi_school_guide_2025", 130, 1, 7) == \
        "gyeonggi_school_guide_2025:p0130:t01r07"
    assert action_id("gyeonggi_school_guide_2025", 166, 3) == "gyeonggi_school_guide_2025:p0166:a03"


@pytest.mark.parametrize("bad", ["TEMP:school_manual:B0025:01", "moe_school_manual_v3:p45:b02",
                                 "moe_school_manual_v3:p0000:b01", "Moe:p0001:b01"])
def test_parse_id_rejects_temp_and_malformed(bad):
    with pytest.raises(IdError):
        parse_id(bad)


def test_id_builder_rejects_zero_page():
    with pytest.raises(IdError):
        chunk_block_id("moe_school_manual_v3", 0, 1)


def test_context_entity_ids():
    assert validate_context_entity_id("Disease:SCARLET_FEVER") == ("Disease", "SCARLET_FEVER")
    label, ident = validate_context_entity_id("SchoolAction:gyeonggi_school_guide_2025:p0166:a03")
    assert (label, ident) == ("SchoolAction", "gyeonggi_school_guide_2025:p0166:a03")
    with pytest.raises(IdError):
        validate_context_entity_id("SCARLET_FEVER")
    with pytest.raises(IdError):
        validate_context_entity_id("Disease:gyeonggi_school_guide_2025:p0166:a03")


# ---------- 계약 임시본 ----------

REQUEST = {"question": "유증상 학생 발견 시 담임·보건교사가 확인할 일은?", "region": "GYEONGGI",
           "allowed_document_ids": ["moe_school_manual_v3", "gyeonggi_school_guide_2025"],
           "preferred_document_ids": ["gyeonggi_school_guide_2025"]}


def _answered():
    chunk = "gyeonggi_school_guide_2025:p0166:t01r01"
    return {"domain": "school", "status": "ANSWERED",
            "findings": [{"claim": "[테스트] 담임교사는 보건교사에게 연락한다(예시 역할).",
                          "chunk_ids": [chunk], "action_ids": ["gyeonggi_school_guide_2025:p0166:a01"],
                          "disease_ids": [], "concept_ids": ["CONTACT_HEALTH_TEACHER"]}],
            "citations": [{"document_id": "gyeonggi_school_guide_2025", "chunk_id": chunk,
                           "pdf_page": 166, "printed_page": 156,
                           "excerpt": "보건교사에게 연락(마스크 착용 필요 여부 확인)"}],
            "action_ids": ["gyeonggi_school_guide_2025:p0166:a01"], "warnings": [], "answer": ""}


def test_valid_input_and_output_pass():
    validate_query_input(REQUEST)
    validate_query_output(_answered(), REQUEST)


@pytest.mark.parametrize("change", [
    {"region": "SEOUL"},
    {"preferred_document_ids": ["kdca_measles_2026"]},
    {"context_entity_ids": ["SCARLET_FEVER"]},
    {"allowed_document_ids": "moe_school_manual_v3"},
])
def test_invalid_input_rejected(change):
    with pytest.raises(ContractError):
        validate_query_input({**REQUEST, **change})


def test_empty_allowed_means_no_documents():
    request = {**REQUEST, "allowed_document_ids": [], "preferred_document_ids": []}
    validate_query_input(request)
    with pytest.raises(ContractError, match="outside allowed"):
        validate_query_output(_answered(), request)


@pytest.mark.parametrize("mutate", [
    lambda r: r["findings"][0].update(chunk_ids=["gyeonggi_school_guide_2025:p0999:b01"]),
    lambda r: r.update(action_ids=[]),
    lambda r: r["citations"][0].update(pdf_page=0),
    lambda r: r["findings"][0].update(concept_ids=["ActionConcept:CONTACT_HEALTH_TEACHER"]),
    lambda r: r.update(internal_state={"x": 1}),
    lambda r: r["citations"].append(dict(r["citations"][0])),
])
def test_invalid_output_rejected(mutate):
    result = _answered()
    mutate(result)
    with pytest.raises(ContractError):
        validate_query_output(result, REQUEST)


def test_empty_outputs_are_valid_and_carry_reason():
    for status in ("INSUFFICIENT_EVIDENCE", "ERROR"):
        validate_query_output(empty_output("school", status, "사유"), REQUEST)
    no_reason = empty_output("school", "ERROR", "사유")
    no_reason["warnings"] = []
    with pytest.raises(ContractError):
        validate_query_output(no_reason, REQUEST)


# ---------- manifest ----------

def test_manifest_has_school_rows():
    with (ROOT / "data/specs/source_manifest.csv").open(encoding="utf-8") as handle:
        rows = {row["document_id"]: row for row in csv.DictReader(handle)}
    assert SCHOOL_DOCS <= set(rows)
    assert rows["moe_school_manual_v3"]["적용지역"] == "NATIONAL"
    assert rows["gyeonggi_school_guide_2025"]["적용지역"] == "GYEONGGI"
    for document_id in rows:
        parse_id(document_id)  # 모든 document_id가 소문자 snake_case
