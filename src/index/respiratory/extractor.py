"""M4 자동 추출 단계: M1 청크에서 학교 행동 후보를 만든다.

blocks.jsonl을 읽어 청크마다 LLM에 보내 학교 행동 후보를 추출한다.
각 후보를 원문 및 허용값과 대조해 문제를 review.issues에 기록하고,
결과를 steps_raw.jsonl에 저장한다.

결과는 후보일 뿐이다. 사람이 원문 대조 후 승인(APPROVED)한 결과만
M5 정규화로 넘겨야 한다. 이 코드는 승인 여부를 자동으로 결정하지 않는다.

실행 위치: 저장소 루트
실행 방법: uv run python -m index.respiratory.extractor --doc 문서ID --pages 116-118
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

# 온톨로지 v3.1(3.1 확정본) 4·7절의 값과 프롬프트 v0 초안에 나온 목록이다.
# 프롬프트 파일의 허용 목록과 반드시 일치해야 하므로 변경할 때 함께 확인한다.
# NEW_CONCEPT_CANDIDATE는 새 개념 검토 표시이며 확정된 개념 ID가 아니다.
ALLOWED_SCOPES = {"COMMON", "DISEASE_GROUP", "NAMED_DISEASES", "UNKNOWN"}
ALLOWED_STAGES = {
    "PREVENTION", "RESPONSE_1", "RESPONSE_2", "RESPONSE_3", "RECOVERY",
    "ALL_STAGES", "NATIONAL_CRISIS", "UNSPECIFIED",
}
ALLOWED_MODALITIES = {"MANDATORY", "RECOMMENDED", "DISCRETIONARY", "UNSPECIFIED"}
ALLOWED_BASES = {"EXPLICIT", "EXAMPLE"}
ALLOWED_DISEASE_IDS = {
    "SCARLET_FEVER", "INFLUENZA", "HFMD", "MEASLES", "COVID19", "VARICELLA",
    "MUMPS", "PERTUSSIS", "MYCOPLASMA_PNEUMONIA", "EKC", "TUBERCULOSIS",
}
ALLOWED_ROLE_IDS = {
    "PRINCIPAL", "VICE_PRINCIPAL", "HEAD_OF_ACADEMIC_AFFAIRS", "GRADE_HEAD",
    "HOMEROOM_TEACHER", "HEALTH_TEACHER", "SUBJECT_TEACHER", "COUNSELOR",
    "ADMIN_OFFICE", "DISCOVERING_STAFF", "INFECTION_MGMT_TEAM",
}
ALLOWED_CONCEPT_IDS = {
    "REPORT_EDU_OFFICE", "NOTIFY_HEALTH_CENTER_LEGAL", "COOPERATE_HEALTH_AUTHORITY",
    "INFORM_GUARDIANS_NOTICE", "CONTACT_GUARDIAN", "DETECT_SYMPTOMATIC",
    "CONTACT_HEALTH_TEACHER", "ESCORT_TO_HEALTH_ROOM", "REQUEST_MEDICAL_VISIT",
    "CHECK_MEDICAL_RESULT", "EXCLUDE_FROM_SCHOOL", "END_EXCLUSION",
    "ISSUE_EXCLUSION_NOTICE", "ACTIVE_SURVEILLANCE", "MANAGE_CLOSE_CONTACTS",
    "MANAGE_HIGH_RISK", "VENTILATE_DISINFECT", "HEALTH_EDUCATION",
    "ADJUST_SCHOOL_ACTIVITIES", "SCHOOL_CLOSURE", "ACTIVATE_RESPONSE_ORG",
    "ATTENDANCE_RECOGNITION", "LEARNING_LOSS_PLAN", "STAFF_DUTY_SUBSTITUTE",
    "MEDIA_COMPLAINT_RESPONSE", "PSYCHOLOGICAL_SUPPORT", "DECLARE_OUTBREAK_END",
    "NEW_CONCEPT_CANDIDATE",
}
ACTION_FIELDS = (
    "action_text", "evidence_quote", "scope", "stage", "step_order", "when_text",
    "timing_text", "modality", "for_disease", "done_by", "instance_of",
)

# 프로젝트 안의 입출력 파일은 실행 위치가 아닌 이 코드의 경로로 찾는다.
# .env는 요청한 대로 현재 위치에서 읽으므로 저장소 루트에서 실행해야 한다.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BLOCKS_PATH = PROJECT_ROOT / "data/interim/respiratory/blocks.jsonl"
PROMPT_PATH = PROJECT_ROOT / "src/index/respiratory/prompts/v0_school_action.md"
OUTPUT_PATH = PROJECT_ROOT / "data/interim/respiratory/steps_raw.jsonl"


# 온톨로지 v3.1 6절의 전역 ID 규약을 적용한다.
# 같은 문서·쪽·행동 순서에는 같은 ID를 써야 재적재할 때 중복을 줄일 수 있다.
# LLM이 행동의 개수나 순서를 바꾸면 번호가 달라질 수 있으므로 재추출 때 대조한다.
def make_action_id(document_id, pdf_page, n):
    """문서·PDF 쪽·그 쪽의 행동 순서로 행동 ID를 생성한다."""
    return f"{document_id}:p{pdf_page:04d}:a{n:02d}"


def parse_pages(value):
    """쉼표로 구분한 쪽수와 범위를 하나의 PDF 쪽수 집합으로 바꾼다.

    예를 들어 15-18,22는 15·16·17·18·22쪽을 선택한다.
    같은 쪽을 여러 번 지정해도 중복 처리하지 않도록 집합을 사용한다.
    """
    pages = set()
    for part in value.split(","):
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part.strip())
        if not match:
            raise argparse.ArgumentTypeError(
                "페이지는 116, 116-118, 15,17 또는 15-18,22 형식이어야 합니다."
            )
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start:
            raise argparse.ArgumentTypeError("PDF 쪽수 범위가 올바르지 않습니다.")
        pages.update(range(start, end + 1))
    return pages


def read_jsonl(path):
    """JSONL을 (원래 줄, 파싱한 객체) 목록으로 읽는다.

    기존 결과에서 교체하지 않는 행을 원래 형태로 보존하기 위해
    파싱한 값뿐 아니라 원래 줄도 함께 보관한다.
    """
    records = []
    with path.open(encoding="utf-8") as file:
        for number, line in enumerate(file, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path} {number}행: JSON 파싱 실패 ({error.msg})") from error
            if not isinstance(obj, dict):
                raise TypeError(f"{path} {number}행: JSON 객체가 아닙니다.")
            records.append((line.rstrip("\r\n"), obj))
    return records


def select_blocks(document_id, pages):
    """요청한 문서 ID와 PDF 쪽 범위의 청크만 입력 순서대로 선택한다."""
    blocks = []
    seen_chunk_ids = set()
    for _, block in read_jsonl(BLOCKS_PATH):
        if block.get("document_id") != document_id:
            continue
        page = block.get("pdf_page")
        if type(page) is not int:
            raise ValueError(f"{document_id}: pdf_page가 정수가 아닌 청크가 있습니다.")
        if page in pages:
            if not isinstance(block.get("chunk_id"), str) or not block["chunk_id"]:
                raise ValueError(f"{document_id} PDF {page}: chunk_id가 없습니다.")
            if not isinstance(block.get("text"), str):
                raise ValueError(f"{block['chunk_id']}: text가 문자열이 아닙니다.")
            # 같은 chunk_id가 두 번 있으면 어느 결과를 교체할지 구분할 수 없다.
            if block["chunk_id"] in seen_chunk_ids:
                raise ValueError(f"입력에 중복된 chunk_id가 있습니다: {block['chunk_id']}")
            seen_chunk_ids.add(block["chunk_id"])
            blocks.append(block)
    if not blocks:
        requested = ",".join(str(page) for page in sorted(pages))
        raise ValueError(f"{document_id}: PDF {requested}에 해당하는 청크가 없습니다.")
    return blocks


def check_id_list(value, name, allowed, issues):
    """ID 목록에 허용되지 않은 값이 있으면 수정하지 않고 사유를 남긴다."""
    if not isinstance(value, list):
        # 검수자는 해당 ID가 누락됐는지, 목록 형식이 올바른지 확인한다.
        issues.append(f"{name}: 배열이 아닙니다.")
        return
    for index, item in enumerate(value):
        if not isinstance(item, str) or item not in allowed:
            # 검수자는 원문과 표준 ID 사전을 대조해 해당 ID가 적절한지 확인한다.
            issues.append(f"{name}[{index}]: 허용되지 않은 ID {item!r}")


# 모델이 같은 순서를 문자와 숫자로 혼용하므로 검수 전에 정수로 통일한다.
def normalize_step_order(value):
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) == 1 and "①" <= text <= "⑳":
        return ord(text) - ord("①") + 1
    match = re.fullmatch(r"([0-9]+)[).]?|\(([0-9]+)\)", text)
    if match:
        return int(match.group(1) or match.group(2))
    return value


def validate_action(action, chunk_diseases, text):
    """한 행동의 누락·허용값·인용문을 점검하고 문제 목록을 반환한다."""
    issues = []
    for field in ACTION_FIELDS:
        if field not in action:
            # 검수자는 빠진 필드가 무엇인지와 원문에 확인할 값이 있는지 살핀다.
            issues.append(f"{field}: 필드가 없습니다.")
    for field in ("action_text", "evidence_quote"):
        value = action.get(field)
        if not isinstance(value, str) or not value.strip():
            # 검수자는 행동 문구나 근거 문장이 실제 원문에 있는지 확인한다.
            issues.append(f"{field}: 비어 있거나 문자열이 아닙니다.")
    for field, allowed in (
        ("scope", ALLOWED_SCOPES), ("stage", ALLOWED_STAGES),
        ("modality", ALLOWED_MODALITIES), ("instance_of", ALLOWED_CONCEPT_IDS),
    ):
        value = action.get(field)
        if not isinstance(value, str) or value not in allowed:
            # 검수자는 해당 필드의 허용 목록과 원문 표현에 맞는 값인지 확인한다.
            issues.append(f"{field}: 허용되지 않은 값 {value!r}")

    order = action.get("step_order")
    if order is not None and (type(order) is not int or order < 1):
        # 검수자는 원문에 실제 순서가 있는지와 번호가 올바른지 확인한다.
        issues.append("step_order: 1 이상의 정수 또는 null이 아닙니다.")
    for field in ("when_text", "timing_text"):
        if action.get(field) is not None and not isinstance(action[field], str):
            # 검수자는 조건·기간이 원문 그대로인 문자열인지 또는 없는 값인지 확인한다.
            issues.append(f"{field}: 문자열 또는 null이 아닙니다.")
    check_id_list(action.get("for_disease"), "for_disease", ALLOWED_DISEASE_IDS, issues)
    check_id_list(chunk_diseases, "chunk_diseases", ALLOWED_DISEASE_IDS, issues)

    roles = action.get("done_by")
    if not isinstance(roles, list):
        # 검수자는 원문에 담당 역할이 명시됐는지와 목록 형식이 맞는지 확인한다.
        issues.append("done_by: 배열이 아닙니다.")
    else:
        for index, role in enumerate(roles):
            if not isinstance(role, dict):
                # 검수자는 해당 담당 정보에 role_id와 basis가 함께 있는지 확인한다.
                issues.append(f"done_by[{index}]: 객체가 아닙니다.")
                continue
            role_id, basis = role.get("role_id"), role.get("basis")
            if not isinstance(role_id, str) or role_id not in ALLOWED_ROLE_IDS:
                # 검수자는 원문의 담당자와 표준 역할 ID가 일치하는지 확인한다.
                issues.append(f"done_by[{index}].role_id: 허용되지 않은 값 {role_id!r}")
            if not isinstance(basis, str) or basis not in ALLOWED_BASES:
                # 검수자는 역할이 원문의 명시인지 예시인지 근거 문구를 확인한다.
                issues.append(f"done_by[{index}].basis: 허용되지 않은 값 {basis!r}")

    # 인용문 비교 때만 연속된 공백·줄바꿈을 한 칸으로 정리한다.
    # MD 변환본은 문장 중간에 줄바꿈이 많아, 그대로 비교하면 같은 문장도
    # 원문에 없는 것으로 잘못 판단할 수 있기 때문이다.
    # 저장하는 evidence_quote는 원문과 대조할 수 있도록 모델이 준 그대로 둔다.
    quote = action.get("evidence_quote")
    if (
        isinstance(quote, str)
        and quote.strip()
        and " ".join(quote.split()) not in " ".join(text.split())
    ):
        # 검수자는 인용문을 해당 청크와 원본 문서에서 직접 대조한다.
        issues.append("evidence_quote: 공백을 정리해도 원문에서 찾을 수 없습니다.")
    return issues


def call_model(client, model, prompt, block):
    """청크 하나를 모델에 보내고 JSON 응답 객체를 읽는다."""
    user_text = json.dumps(
        {
            "document_id": block["document_id"],
            "pdf_page": block["pdf_page"],
            "text": block["text"],
        },
        ensure_ascii=False,
    )
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_text},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    # GPT-6 Luna는 추론 수준이 none이 아닐 때 temperature를 지원하지 않는다.
    # 사용자가 요청한 temperature=0을 적용하기 위해 Luna에만 none을 지정한다.
    if model == "gpt-6-luna":
        request["reasoning_effort"] = "none"
    completion = client.chat.completions.create(**request)
    if not completion.choices:
        raise ValueError(f"{block['chunk_id']}: 모델 응답이 비어 있습니다.")
    choice = completion.choices[0]
    raw = choice.message.content
    if choice.finish_reason != "stop" or not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{block['chunk_id']}: 응답이 비었거나 완료되지 않았습니다.")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as error:
        # 파싱 실패 응답을 청크 ID와 함께 남겨 원인을 따로 확인할 수 있게 한다.
        print(f"파싱 실패 응답 ({block['chunk_id']}):\n{raw}", file=sys.stderr)
        raise ValueError(f"{block['chunk_id']}: 모델 응답 JSON 파싱 실패") from error
    if not isinstance(result, dict) or not isinstance(result.get("actions"), list):
        print(f"형식 오류 응답 ({block['chunk_id']}):\n{raw}", file=sys.stderr)
        raise TypeError(f"{block['chunk_id']}: JSON 객체 또는 actions 배열이 없습니다.")
    return result


def source_chunk_id(item):
    """기존 결과가 어느 청크에서 나왔는지 확인한다."""
    review = item.get("review")
    return review.get("source_chunk_id") if isinstance(review, dict) else None


def merge_results(existing, new_actions, successful_chunk_ids):
    """성공한 청크의 옛 행을 모두 빼고 새 행을 추가한다.

    행동 수가 줄어들어도 옛 a03 같은 행이 남지 않도록 청크 단위로 교체한다.
    실패한 청크와 이번에 처리하지 않은 청크는 원래 행을 그대로 유지한다.
    """
    lines = []
    for original, item in existing:
        if source_chunk_id(item) not in successful_chunk_ids:
            lines.append(original)
    lines.extend(json.dumps(item, ensure_ascii=False) for item in new_actions)
    return lines


def main():
    parser = argparse.ArgumentParser(description="호흡기 지침 학교 행동 후보 추출")
    parser.add_argument("--doc", required=True, help="처리할 document_id")
    parser.add_argument(
        "--pages", required=True, type=parse_pages, help="PDF 쪽: 116, 116-118, 15,17"
    )
    args = parser.parse_args()

    if Path.cwd().resolve() != PROJECT_ROOT:
        raise ValueError(f"저장소 루트에서 실행해 주세요: {PROJECT_ROOT}")
    load_dotenv(".env")
    model = os.getenv("OPENAI_MODEL", "").strip()
    if not model:
        raise ValueError(".env에 OPENAI_MODEL이 없습니다.")
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError(".env 또는 환경변수에 OPENAI_API_KEY가 없습니다.")
    if not PROMPT_PATH.is_file():
        raise ValueError(f"프롬프트 파일이 없습니다: {PROMPT_PATH}")
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    if not prompt.strip():
        raise ValueError(f"프롬프트 파일이 비어 있습니다: {PROMPT_PATH}")
    blocks = select_blocks(args.doc, args.pages)

    # 기존 파일 자체의 JSON이 잘못됐다면 교체 대상을 안전하게 판별할 수 없다.
    existing = read_jsonl(OUTPUT_PATH) if OUTPUT_PATH.exists() else []
    client = OpenAI()
    successful_results = []
    failures = []

    for block in blocks:
        try:
            result = call_model(client, model, prompt, block)
            diseases = result.get("chunk_diseases")
            candidates = []
            for action in result["actions"]:
                if not isinstance(action, dict):
                    raise TypeError("actions에 객체가 아닌 항목이 있습니다.")
                action["step_order"] = normalize_step_order(action.get("step_order"))
                issues = validate_action(action, diseases, block["text"])
                candidates.append((action, issues))

            # 청크 전체가 정상 처리된 후에만 성공 목록에 넣는다.
            # 중간에 실패한 청크의 기존 행을 실수로 지우지 않기 위해서다.
            successful_results.append((block, diseases, candidates))
        except (OpenAIError, ValueError, TypeError, KeyError, IndexError) as error:
            # 한 청크의 호출·파싱 실패가 나머지 청크의 처리를 막지 않게 한다.
            # 실패한 청크는 성공 목록에 없으므로 기존 결과도 그대로 보존된다.
            failures.append((block["chunk_id"], f"{type(error).__name__}: {error}"))

    successful_chunk_ids = {block["chunk_id"] for block, _, _ in successful_results}

    # 보존할 기존 행의 action_id는 사용 중인 번호로 남겨 둔다.
    # 같은 쪽의 다른 청크가 실패해 그 옛 결과를 보존하는 경우에도
    # 새 행동이 동일한 ID를 가져 중복되는 상황을 방지하기 위해서다.
    reserved_ids = {
        item["action_id"]
        for _, item in existing
        if source_chunk_id(item) not in successful_chunk_ids
        and isinstance(item.get("action_id"), str)
    }
    page_counts = Counter()
    actions = []
    no_actions = []

    for block, diseases, candidates in successful_results:
        if not candidates:
            # 행동이 없는 청크의 질병 후보는 행동 행이 없어 화면에 따로 표시한다.
            issues = []
            check_id_list(diseases, "chunk_diseases", ALLOWED_DISEASE_IDS, issues)
            no_actions.append((block["chunk_id"], diseases, issues))
        for action, issues in candidates:
            page = block["pdf_page"]
            page_counts[page] += 1
            action_id = make_action_id(block["document_id"], page, page_counts[page])
            while action_id in reserved_ids:
                page_counts[page] += 1
                action_id = make_action_id(block["document_id"], page, page_counts[page])
            reserved_ids.add(action_id)
            actions.append({
                "action_id": action_id,
                **{field: action.get(field) for field in ACTION_FIELDS},
                "supported_by": [block["chunk_id"]],
                "chunk_diseases": diseases,
                "review": {
                    "status": "PENDING",
                    "issues": issues,
                    "model": model,
                    "prompt_version": "v0",
                    "source_chunk_id": block["chunk_id"],
                },
            })

    # 성공한 청크가 하나라도 있을 때만 그 청크들의 옛 행을 교체한다.
    # 성공 결과가 행동 0개여도 해당 청크의 옛 행동은 더 이상 유효하지 않다.
    if successful_chunk_ids:
        lines = merge_results(existing, actions, successful_chunk_ids)
        with OUTPUT_PATH.open("w", encoding="utf-8") as file:
            if lines:
                file.write("\n".join(lines) + "\n")

    flagged = [action for action in actions if action["review"]["issues"]]
    print(f"처리 블록 수: {len(blocks)}")
    print(f"추출 행동 수: {len(actions)}")
    print(f"issues 있는 행동 수: {len(flagged)}")
    print("행동별 issues:")
    if not flagged:
        print("  없음")
    for action in flagged:
        for issue in action["review"]["issues"]:
            print(f"  {action['action_id']}: {issue}")
    if no_actions:
        print("행동이 없는 청크의 질병 후보와 검수 항목:")
        for chunk_id, diseases, issues in no_actions:
            print(f"  {chunk_id}: chunk_diseases={json.dumps(diseases, ensure_ascii=False)}")
            for issue in issues:
                print(f"    {issue}")
    print("실패 청크:")
    if not failures:
        print("  없음")
    for chunk_id, reason in failures:
        print(f"  {chunk_id}: {reason}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        print(f"오류: {error}", file=sys.stderr)
        sys.exit(1)
