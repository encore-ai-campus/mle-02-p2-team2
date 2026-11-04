"""저장된 학교 행동 후보를 노트북 안에서 검수하는 화면."""

import hashlib
import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path

import ipywidgets as widgets


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def save_reviews(folder, rows):
    """완성한 검수 기록을 저장한다. 원래 LLM 후보는 수정하지 않는다."""
    path = Path(folder) / "review.jsonl"
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def candidate_key(candidate):
    return hashlib.sha256(json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def concept_options():
    """개념 드롭다운 항목: (표시, 값). ''은 LLM 후보를 그대로 쓴다는 뜻.

    표준 개념만 보여 준다. 맞는 개념이 없으면 고르지 않아도 된다(행동은 개념 연결 없이 적재).
    """
    from index.school.dictionaries import load_dictionary

    book = load_dictionary("concept")
    options = [("LLM 후보 그대로 사용", "")]
    options += [(f"{e['concept_id']} · {e['name']}", e["concept_id"]) for e in book["entries"]]
    return options


def create_review_ui(base_dir):
    """추출 성공 기록을 골라 후보별 원문과 판정을 확인·저장한다."""
    folders = []
    for path in sorted(Path(base_dir).glob("*/run.jsonl"), reverse=True):
        runs = read_jsonl(path)
        if runs and runs[0].get("status") == "SUCCESS" and read_jsonl(path.parent / "steps_raw.jsonl"):
            folders.append(path.parent)
    if not folders:
        return widgets.HTML("저장된 행동 후보가 없습니다. 먼저 추출 결과 저장 셀을 실행하세요.")

    run = widgets.Dropdown(options=[(folder.name, folder) for folder in folders], description="추출 결과:", layout={"width": "95%"})
    picker = widgets.Dropdown(description="행동 후보:", layout={"width": "95%"})
    detail = widgets.HTML()
    reviewer = widgets.Text(description="검수자:")
    status = widgets.Dropdown(options=[("미검수", "PENDING"), ("승인", "APPROVED"), ("확인 필요", "NEEDS_REVIEW"), ("반려", "REJECTED")], description="판정:")
    note = widgets.Textarea(description="판정 메모:", placeholder="수정할 점, 보류·반려 이유 등을 적으세요.", layout={"width": "95%", "height": "85px"})
    correction = widgets.Textarea(description="행동 수정안:", placeholder="수정이 필요할 때만 입력하세요. 원래 LLM 후보와 별도로 저장합니다.", layout={"width": "95%", "height": "65px"})
    # 개념 선택(선택 사항): 비워 두면 LLM 후보를 쓴다. 표준 개념이 아니면 개념 연결만 생략하고 행동은 적재된다.
    concept = widgets.Dropdown(options=concept_options(), description="개념 선택:", layout={"width": "95%"})
    labels = {
        "pdf_source_checked": "PDF 판본·쪽수·근거 확인",
        "role_row_checked": "같은 행의 역할과 행동 확인",
        "example_basis_checked": "역할의 예시·명시 여부 확인",
        "stage_condition_checked": "단계·조건·기한·강도 확인",
        "concept_checked": "개념·질병 연결 확인",
    }
    checks = {key: widgets.Checkbox(description=label, indent=False, layout={"width": "auto"}) for key, label in labels.items()}
    save = widgets.Button(description="현재 판정 저장", button_style="primary")
    next_button = widgets.Button(description="저장 후 다음 후보")
    message = widgets.HTML()
    progress = widgets.HTML()
    state = {}

    def render(_=None):
        if picker.value is None:
            return
        index = picker.value
        candidate = state["candidates"][index]
        record = state["reviews"][index]
        chunk = state["chunks"].get(candidate["source_chunk_id"], {})
        def box(title, content):
            return f"<h4>{escape(title)}</h4><pre style='white-space:pre-wrap'>{escape(str(content))}</pre>"
        content = box("행동 후보", candidate["action_text"])
        content += box("역할·단계·조건·개념", json.dumps({key: candidate.get(key) for key in ("roles", "stage", "step_order", "when_text", "timing_text", "modality", "scope", "named_diseases", "concept_candidate")}, ensure_ascii=False, indent=2))
        content += box("모델이 인용한 근거", candidate.get("evidence", ""))
        content += box("자동 검사에서 남긴 사항", candidate.get("check_reasons", []))
        content += box("출처", f"{candidate['document_id']} / PDF {chunk.get('pdf_page')}쪽 / 인쇄 {chunk.get('printed_page')}쪽\n{chunk.get('source_path', '')}\n{chunk.get('section', '')}")
        for part in chunk.get("source_parts", []):
            content += box(f"원문 구성요소: {part['part']}", part["text"])
        for context in chunk.get("editorial_context", []):
            content += box("표 작업자의 참고 메모 (원문 인용과 구분)", context["text"])
        detail.value = content
        reviewer.value = record.get("reviewer") or reviewer.value
        status.value = record.get("review_status", "PENDING")
        note.value = record.get("reason") or ""
        correction.value = record.get("edited_action_text") or ""
        known = {value for _, value in concept.options}
        concept.value = record.get("edited_concept_id") if record.get("edited_concept_id") in known else ""
        for key, checkbox in checks.items():
            checkbox.value = bool(record.get(key))
        completed = sum(row.get("review_status") != "PENDING" for row in state["reviews"])
        progress.value = f"<b>{index + 1} / {len(state['candidates'])}번째 후보 · 판정 기록 {completed}건</b>"
        message.value = ""

    def load(_=None):
        folder = Path(run.value)
        candidates = read_jsonl(folder / "steps_raw.jsonl")
        previous = read_jsonl(folder / "review.jsonl") if (folder / "review.jsonl").exists() else []
        reviews = []
        for index, candidate in enumerate(candidates, 1):
            fingerprint = candidate_key(candidate)
            record = next((row for row in previous if row.get("candidate_fingerprint") == fingerprint), None)
            if record is None:
                record = next((row for row in previous if not row.get("candidate_fingerprint") and row.get("candidate_index") == index and row.get("source_chunk_id") == candidate["source_chunk_id"]), {})
            reviews.append({**record, "document_id": candidate["document_id"], "source_chunk_id": candidate["source_chunk_id"], "candidate_index": index, "candidate_fingerprint": fingerprint, "review_status": record.get("review_status", "PENDING")})
        state.update(folder=folder, candidates=candidates, reviews=reviews, chunks={chunk["chunk_id"]: chunk for chunk in read_jsonl(folder / "chunks.jsonl")})
        picker.options = [(f"{i + 1}. {candidate['action_text'][:65]}", i) for i, candidate in enumerate(candidates)]
        picker.value = 0
        render()

    def persist(advance=False):
        if not reviewer.value.strip():
            message.value = "검수자 이름을 입력하세요."
            return
        if status.value == "APPROVED" and not all(check.value for check in checks.values()):
            message.value = "승인하려면 위의 다섯 항목을 확인하세요. 확인이 남았다면 ‘확인 필요’로 저장할 수 있습니다."
            return
        if status.value in ("REJECTED", "NEEDS_REVIEW") and not note.value.strip():
            message.value = "반려 또는 확인이 필요한 이유를 메모에 적어 주세요."
            return
        index = picker.value
        state["reviews"][index].update(review_status=status.value, reviewer=reviewer.value.strip(), reviewed_at=datetime.now(UTC).isoformat(), reason=note.value.strip(), edited_action_text=correction.value.strip() or None, edited_concept_id=concept.value or None, evidence_location=state["candidates"][index]["source_chunk_id"], **{key: check.value for key, check in checks.items()})
        save_reviews(state["folder"], state["reviews"])
        if advance and index + 1 < len(state["candidates"]):
            picker.value = index + 1
        else:
            render()
            message.value = "현재 판정을 저장했습니다."

    picker.observe(render, names="value")
    run.observe(load, names="value")
    save.on_click(lambda _: persist())
    next_button.on_click(lambda _: persist(advance=True))
    load()
    return widgets.VBox([run, widgets.HTML("후보를 살펴보고 판정을 입력한 뒤 저장하세요. 후보 선택을 바꾸기 전에 저장해야 입력이 남습니다."), picker, progress, detail, reviewer, status, widgets.VBox(list(checks.values())), concept, correction, note, widgets.HBox([save, next_button]), message])
