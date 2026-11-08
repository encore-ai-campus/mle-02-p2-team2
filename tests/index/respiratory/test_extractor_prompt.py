import json
import subprocess
import sys

import pytest

from index.respiratory import extractor, normalize
from preprocess.respiratory import md_to_blocks


def test_v1_prompt_keeps_contract_ids_and_table_evidence_rule():
    prompt = extractor.load_prompt("v1")
    for field in extractor.ACTION_FIELDS:
        assert f'"{field}"' in prompt
    for value in (
        extractor.ALLOWED_DISEASE_IDS
        | extractor.ALLOWED_ROLE_IDS
        | extractor.ALLOWED_CONCEPT_IDS
        | extractor.ALLOWED_SCOPES
        | extractor.ALLOWED_STAGES
        | extractor.ALLOWED_MODALITIES
        | extractor.ALLOWED_BASES
    ):
        assert value in prompt
    assert "해당 데이터 행에서 그대로 복사한 연속 구절" in prompt
    assert "다른 행 또는 다른 쪽" in prompt


def test_prompt_selection_rejects_unknown_versions():
    assert extractor.load_prompt("v0") == extractor.PROMPT_PATH.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="지원하지 않는 프롬프트 버전"):
        extractor.load_prompt("unverified")


def test_table_evidence_must_come_from_row_and_keep_review_flags():
    action = {
        "action_text": "안내한다", "evidence_quote": "담당교사", "scope": "UNKNOWN",
        "stage": "UNSPECIFIED", "step_order": None, "when_text": None,
        "timing_text": None, "modality": "UNSPECIFIED", "for_disease": [],
        "done_by": [], "instance_of": "NEW_CONCEPT_CANDIDATE",
    }
    block = {
        "chunk_id": "sample:p0001:t01r01", "text": "담당교사\n| 행동 |\n| 보호자에게 안내한다 |",
        "row_text": "| 보호자에게 안내한다 |", "review_flags": ["PDF 원문 확인 필요"],
    }
    issues = extractor.validate_block_action(action, [], block)
    assert any("해당 행 원문" in issue for issue in issues)
    assert "표 행 검수 필요: PDF 원문 확인 필요" in issues
    action["evidence_quote"] = "보호자에게 안내한다"
    block["review_flags"] = []
    assert extractor.validate_block_action(action, [], block) == []
    del block["row_text"]
    assert "표 행 원문이 없어 근거를 검증할 수 없습니다." in extractor.validate_block_action(action, [], block)


def test_normalize_does_not_approve_header_as_row_evidence(monkeypatch):
    action_id = "sample:p0001:a01"
    chunk_id = "sample:p0001:t01r01"
    monkeypatch.setitem(normalize.REVIEW_DECISIONS, action_id, "APPROVE")
    row = {
        "action_id": action_id, "action_text": "안내한다", "evidence_quote": "담당교사",
        "scope": "UNKNOWN", "stage": "UNSPECIFIED", "step_order": None,
        "when_text": None, "timing_text": None, "modality": "UNSPECIFIED", "for_disease": [],
        "done_by": [], "instance_of": "HEALTH_EDUCATION", "supported_by": [chunk_id],
        "chunk_diseases": [], "review": {},
    }
    block = {
        "chunk_id": chunk_id, "document_id": "sample", "pdf_page": 1,
        "text": "담당교사\n| 행동 |\n| 보호자에게 안내한다 |",
        "row_text": "| 보호자에게 안내한다 |", "review_flags": [],
    }
    rejected = normalize.normalize_row(row, {chunk_id: block})
    assert rejected["review"]["status"] == "PENDING"
    row["evidence_quote"] = "보호자에게 안내한다"
    accepted = normalize.normalize_row(row, {chunk_id: block})
    assert accepted["review"]["status"] == "APPROVED"


def test_table_selection_uses_only_requested_document_and_page():
    rows = [
        ("", {"document_id": "first", "pdf_page": 2, "chunk_id": "first:p0002:t01r01", "text": "행"}),
        ("", {"document_id": "first", "pdf_page": 3, "chunk_id": "first:p0003:t01r01", "text": "행"}),
        ("", {"document_id": "second", "pdf_page": 2, "chunk_id": "second:p0002:t01r01", "text": "행"}),
    ]
    assert [row["chunk_id"] for row in extractor.select_blocks("first", {2}, rows)] == [
        "first:p0002:t01r01"
    ]


def test_table_cli_requires_separate_output_before_model_access():
    completed = subprocess.run(
        [sys.executable, "-m", "index.respiratory.extractor", "--doc", "first", "--pages", "2", "--table-rows"],
        cwd=extractor.PROJECT_ROOT, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 2
    assert "--candidate-output" in completed.stderr


def test_table_cli_stages_pending_candidate_without_overwriting_old_output(monkeypatch, tmp_path):
    markdown = tmp_path / "input.md"
    markdown.write_text(
        "## PDF 3페이지\n### 예방 조치\n| 대상 | 행동 |\n| --- | --- |\n| 교사 | 교실을 환기한다 |\n"
    )
    outdir = tmp_path / "notebooks/respiratory"
    outdir.mkdir(parents=True)
    outfile = outdir / "candidate.jsonl"
    existing = tmp_path / "existing.jsonl"
    existing.write_text("기존 결과 보존")
    monkeypatch.setattr(extractor, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extractor, "OUTPUT_PATH", existing)
    monkeypatch.setattr(md_to_blocks, "input_files", lambda: {"sample": markdown})
    monkeypatch.setattr(extractor, "load_dotenv", lambda *_args: None)
    monkeypatch.setattr(extractor, "OpenAI", lambda: object())
    monkeypatch.setenv("OPENAI_MODEL", "mock-model")
    monkeypatch.setenv("OPENAI_API_KEY", "mock-key")
    action = {
        "action_text": "교실을 환기한다", "evidence_quote": "교실을 환기한다", "scope": "UNKNOWN",
        "stage": "UNSPECIFIED", "step_order": None, "when_text": None, "timing_text": None,
        "modality": "UNSPECIFIED", "for_disease": [], "done_by": [], "instance_of": "VENTILATE_DISINFECT",
    }
    monkeypatch.setattr(extractor, "call_model", lambda *_args: {"chunk_diseases": [], "actions": [action]})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "extractor", "--doc", "sample", "--pages", "3", "--table-rows", "--prompt-version", "v1",
        "--candidate-output", str(outfile),
    ])
    extractor.main()
    candidate = json.loads(outfile.read_text().strip())
    assert existing.read_text() == "기존 결과 보존"
    assert candidate["supported_by"] == ["sample:p0003:t01r01"]
    assert candidate["review"]["status"] == "PENDING"
    assert candidate["review"]["action_id_provisional"] is True
    assert any("PDF 표 제목" in item for item in candidate["review"]["issues"])
