"""학교 Markdown의 본문과 표를 문서·소스·블록 DataFrame으로 준비한다.

01번 노트북의 분리 규칙을 재사용하기 위해 옮긴 독립 모듈이다.
01번 노트북의 규칙이 바뀌면 이 모듈과 결과를 함께 비교해야 한다.
"""

import hashlib
import re
from pathlib import Path

import pandas as pd

# 자주 쓸 정규식을 미리 준비합니다. r"..."은 역슬래시를 정규식에 그대로 전달합니다.
# PAGE는 실제 확인된 "## PDF 12페이지" 형태만 페이지 번호로 인식합니다.
PAGE = re.compile(r"^## PDF ([0-9]+)페이지\s*$")
# 제목의 # 개수와 제목 내용을 각각 그룹으로 잡아 계층 경로에 사용합니다.
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$")
# 코드 펜스는 ``` 또는 ~~~가 3개 이상 반복되는 시작 줄입니다.
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# HTML 표의 시작·끝을 따로 찾아 내부의 빈 줄이나 #을 경계로 오인하지 않습니다.
HTML_TABLE = re.compile(r"^\s*<table\b", re.IGNORECASE)
HTML_END = re.compile(r"</table\s*>", re.IGNORECASE)
# 파이프 표는 머리글 다음에 | --- | --- | 같은 구분 행이 있어야 인정합니다.
SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
# 이미지 링크, HTML 주석, Markdown 구분선은 삭제하지 않고 other로 남깁니다.
OTHER = re.compile(r"^\s*(?:!\[|<!--|(?:[-*_]\s*){3,}$)")

def markdown_blocks(text: str) -> list[dict]:
    """원문을 편집하지 않고 순서·위치가 있는 블록으로 분리한다."""
    # 줄바꿈까지 포함해서 나눠야 각 줄의 원래 문자 위치를 정확히 계산할 수 있습니다.
    lines = text.splitlines(keepends=True)
    # offsets[i]는 i번째 줄이 원문 문자열에서 시작하는 위치입니다.
    # 예: "가\n나"라면 두 번째 줄의 시작 위치는 2입니다.
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)
    # blocks는 결과 목록, headings는 현재 제목 계층, page는 현재 PDF 쪽수입니다.
    blocks = []
    headings = {}
    page = None

    # 내부 함수: [start, end) 줄 범위를 원문 문자 범위로 바꿔 블록 하나를 추가합니다.
    # end는 포함하지 않는 끝 위치이므로 Python 슬라이싱과 같은 규칙입니다.
    def add(start, end, kind):
        # 마지막 블록은 다음 줄의 offset이 없으므로 원문 전체 길이를 끝으로 사용합니다.
        first = offsets[start]
        last = offsets[end] if end < len(lines) else len(text)
        # 1부터 시작하는 문서 내 순서와 B0001 같은 임시 블록 ID를 만듭니다.
        order = len(blocks) + 1
        # 원문 조각은 text[first:last] 그대로 보관해 나중에 출처로 되짚을 수 있습니다.
        blocks.append({
            "block_order": order, "block_id": f"B{order:04d}",
            "section": " > ".join(headings.values()), "kind": kind,
            "pdf_page": page, "text": text[first:last],
            "start_offset": first, "end_offset": last,
        })

    # 현재 줄에 |가 있고 바로 다음 줄이 표 구분 행인지 함께 확인합니다.
    def table_start(i):
        return i + 1 < len(lines) and "|" in lines[i] and bool(SEPARATOR.match(lines[i + 1]))

    # 일반 문단을 읽다가 다른 구조의 시작을 만나면 그 앞에서 멈추기 위한 검사입니다.
    def special(i):
        line = lines[i]
        return bool(PAGE.match(line) or HEADING.match(line) or FENCE.match(line)
                    or HTML_TABLE.match(line) or OTHER.match(line) or table_start(i))

    # i는 현재 읽는 줄 번호입니다. 각 구조를 처리하면 다음 미처리 줄로 이동합니다.
    i = 0
    while i < len(lines):
        line = lines[i]
        # 블록 사이의 빈 줄은 별도 블록으로 만들지 않습니다. 원문은 documents_df에 남습니다.
        if not line.strip():
            i += 1
            continue
        # 페이지 구분자는 먼저 처리해 일반 제목 계층에 포함되지 않게 합니다.
        match = PAGE.match(line)
        if match:
            # 정규식 첫 번째 괄호의 숫자를 정수로 바꿉니다. 이후 블록에 현재 쪽수를 붙입니다.
            page = int(match.group(1))
            add(i, i + 1, "other")
            i += 1
            continue
        # 코드 블록은 닫는 펜스까지 한 덩어리로 보존합니다.
        match = FENCE.match(line)
        if match:
            marker = match.group(1)
            # 시작과 같은 문자(` 또는 ~)가 시작 길이 이상 반복돼야 닫는 펜스입니다.
            closing = re.compile(r"^ {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$")
            end = i + 1
            while end < len(lines) and not closing.match(lines[end]):
                end += 1
            # 닫는 줄이 있으면 포함하고, 없어도 원문 끝을 넘지 않도록 합니다.
            end = min(end + 1, len(lines))
            add(i, end, "code")
            i = end
            continue
        # HTML 표는 닫는 </table>까지 줄바꿈을 유지합니다.
        if HTML_TABLE.match(line):
            end = i + 1
            while end < len(lines) and not HTML_END.search(lines[end - 1]):
                end += 1
            add(i, end, "table")
            i = end
            continue
        # Markdown 표는 머리글·구분 행을 포함하고 이어지는 | 줄을 함께 묶습니다.
        if table_start(i):
            end = i + 2
            while end < len(lines) and lines[end].strip() and "|" in lines[end]:
                end += 1
            add(i, end, "table")
            i = end
            continue
        # 제목은 자신의 레벨 이상인 기존 하위 제목을 지우고 새 제목을 기록합니다.
        match = HEADING.match(line)
        if match:
            level = len(match.group(1))
            # 예: 새 ## 제목이 나오면 이전 ##·###은 지우고 상위 #은 유지합니다.
            headings = {key: value for key, value in headings.items() if key < level}
            headings[level] = match.group(2).strip()
            add(i, i + 1, "heading")
            i += 1
            continue
        # 이미지·구분선은 한 줄, 여러 줄 HTML 주석은 닫는 표시까지 보존합니다.
        if OTHER.match(line):
            end = i + 1
            if line.lstrip().startswith("<!--") and "-->" not in line:
                while end < len(lines) and "-->" not in lines[end - 1]:
                    end += 1
            add(i, end, "other")
            i = end
            continue
        # 어느 특수 구조에도 해당하지 않으면 빈 줄 또는 다음 구조 전까지 일반 텍스트입니다.
        end = i + 1
        while end < len(lines) and lines[end].strip() and not special(end):
            end += 1
        add(i, end, "text")
        i = end
    # 순서가 있는 딕셔너리 목록을 반환합니다. 이 단계에서 LLM 청크를 만들지는 않습니다.
    return blocks

# 표 제목의 PDF 쪽수와 인쇄 쪽수(선택적 범위)를 구합니다.
TABLE_PAGE = re.compile(r"^##\s+PDF\s*(\d+)(?:\s*[~～-]\s*(\d+))?\s*(?:쪽|페이지)")
PRINTED_PAGE = re.compile(r"인쇄\s*(\d+)(?:\s*[~～-]\s*(\d+))?\s*쪽")

def annotate_table_blocks(blocks, source_id):
    """원문 순서의 블록에 표 묶음, 쪽수 범위와 검토용 역할을 더한다."""
    group_no = 0
    group_id = title = None
    page_start = page_end = printed_start = printed_end = None
    for block in blocks:
        # ## 표 제목은 새 묶음의 시작입니다. 표 파일 내 번호는 임시 식별자입니다.
        heading = block["text"].strip() if block["kind"] in ("heading", "other") else ""
        match = TABLE_PAGE.match(heading)
        if match:
            group_no += 1
            group_id = f"{source_id}:G{group_no:04d}"
            title = heading.removeprefix("##").strip()
            page_start = int(match.group(1))
            page_end = int(match.group(2) or match.group(1))
            printed = PRINTED_PAGE.search(heading)
            printed_start = int(printed.group(1)) if printed else None
            printed_end = int(printed.group(2) or printed.group(1)) if printed else None
        elif block["kind"] == "heading" and heading.startswith("### ") and page_start is not None:
            # 일부 표 파일은 PDF 페이지 제목 아래 ### 표 제목을 별도로 둡니다.
            group_no += 1
            group_id = f"{source_id}:G{group_no:04d}"
            title = heading.removeprefix("###").strip()
        elif heading.startswith(("# ", "## ")):
            # 문서 제목·검증 범위처럼 표가 아닌 제목은 이전 표에 묶지 않습니다.
            group_id = title = None
            page_start = page_end = printed_start = printed_end = None

        block.update({
            "table_group_id": group_id, "table_title": title,
            "pdf_page": page_start, "pdf_page_end": page_end,
            "printed_page": printed_start, "printed_page_end": printed_end,
            "context_role": ("table_title" if match or (heading.startswith("### ") and group_id) else
                             "table" if group_id and block["kind"] == "table" else
                             "supplement_unclassified" if group_id else "document_note"),
        })
    return blocks



def load_school_data(root):
    """Markdown 4개를 읽고 원문 범위를 검증해 세 DataFrame을 반환한다."""
    ROOT = Path(root).resolve()
    DATA_DIR = ROOT / "data" / "raw"
    # 파일명을 명시해 data/raw에 다른 Markdown이 추가되어도 섞이지 않게 합니다.
    document_specs = [
        {"doc_id": "school_manual", "title": "학교 감염병 예방·위기대응 매뉴얼 제3차 개정판",
         "body": "학교 감염병 예방·위기대응 매뉴얼 제3차 개정판(초중고·특수학교용).md",
         "tables": "학교_위기대응_매뉴얼_본문_표.md"},
        {"doc_id": "gyeonggi_guide", "title": "경기도 학교 감염병관리 실무가이드북",
         "body": "(본책) 경기도 학교 감염병관리 실무가이드북.md",
         "tables": "경기도_실무가이드북_본문_표.md"},
    ]

    documents_df = pd.DataFrame([{"doc_id": d["doc_id"], "title": d["title"]} for d in document_specs])
    source_rows = []
    for doc in document_specs:
        for source_type in ("body", "tables"):
            path = DATA_DIR / doc[source_type]
            if not path.is_file():
                raise FileNotFoundError(f"입력 파일을 찾지 못했습니다: {path}")
            text = path.read_text(encoding="utf-8-sig")
            source_rows.append({
                "doc_id": doc["doc_id"], "source_id": f"{doc['doc_id']}:{source_type}",
                "source_type": source_type, "file_name": path.name,
                "source_path": path.relative_to(ROOT).as_posix(), "text": text,
                "char_count": len(text),
                # 변환된 Markdown 텍스트의 변경 감지용이며 원본 PDF 해시는 아닙니다.
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            })
    sources_df = pd.DataFrame(source_rows)
    assert documents_df["doc_id"].is_unique and sources_df["source_id"].is_unique
    assert sources_df["text"].str.strip().ne("").all()
    block_rows = []
    for source in sources_df.itertuples(index=False):
        blocks = markdown_blocks(source.text)
        if source.source_type == "tables":
            blocks = annotate_table_blocks(blocks, source.source_id)
        else:
            for block in blocks:
                # 본문 쪽수는 명시된 PDF 페이지이고 인쇄 쪽수는 추정하지 않습니다.
                block.update({"pdf_page_end": block["pdf_page"], "printed_page": None,
                              "printed_page_end": None, "table_group_id": None,
                              "table_title": None, "context_role": "body"})
        for block in blocks:
            block_rows.append({"doc_id": source.doc_id, "source_id": source.source_id,
                               "source_type": source.source_type, "file_name": source.file_name,
                               "content_hash": source.content_hash, **block})

    blocks_df = pd.DataFrame(block_rows)
    if blocks_df.empty:
        raise ValueError("생성된 블록이 없습니다.")
    blocks_df["char_count"] = blocks_df["text"].str.len()
    for column in ("pdf_page", "pdf_page_end", "printed_page", "printed_page_end"):
        blocks_df[column] = blocks_df[column].astype("Int64")

    # assert는 조건이 거짓이면 실행을 중단합니다.
    # block_id는 파일마다 다시 B0001부터 시작하므로 source_id와 함께 중복을 검사합니다.
    assert not blocks_df[["source_id", "block_id"]].duplicated().any()
    # 텍스트가 누락되거나 공백뿐인 블록이 있는지 확인합니다.
    # notna()는 결측값, strip()은 앞뒤 공백, ne("")는 빈 문자열이 아님을 검사합니다.
    assert blocks_df["text"].notna().all() and blocks_df["text"].str.strip().ne("").all()
    # 입력된 네 소스 모두 블록 생성 결과에 포함됐는지 소스 ID 집합을 비교합니다.
    assert set(blocks_df["source_id"]) == set(sources_df["source_id"])
    # {source_id: Markdown 원문} 형태의 사전을 만들어 블록의 출처별 원문을 찾습니다.
    originals = sources_df.set_index("source_id")["text"].to_dict()
    # source_id별로 묶어 각 파일의 원문과 해당 파일의 블록만 비교합니다.
    for source_id, group in blocks_df.groupby("source_id", sort=False):
        original = originals[source_id]
        # 블록 순서가 1부터 빠짐없이 이어지는지 확인합니다.
        assert group["block_order"].tolist() == list(range(1, len(group) + 1))
        # 직전 블록의 끝 위치입니다. 첫 블록은 원문 시작인 0부터 비교합니다.
        previous_end = 0
        for block in group.itertuples(index=False):
            # 범위가 이전 블록과 겹치지 않고 원문 안에 있는지 확인합니다.
            # start_offset은 포함, end_offset은 포함하지 않는 Python 슬라이싱 기준입니다.
            assert previous_end <= block.start_offset < block.end_offset <= len(original), block.block_id
            # 두 블록 사이에 생략된 부분이 있다면 공백뿐이어야 합니다.
            assert not original[previous_end:block.start_offset].strip(), block.block_id
            # 기록된 위치로 원문을 다시 잘라 블록 텍스트와 정확히 같은지 확인합니다.
            assert original[block.start_offset:block.end_offset] == block.text, block.block_id
            # 다음 블록 검사에 사용할 직전 끝 위치를 갱신합니다.
            previous_end = block.end_offset
        # 마지막 블록 뒤에도 공백이 아닌 글자가 남아 있지 않아야 합니다.
        assert not original[previous_end:].strip(), source_id


    return documents_df, sources_df, blocks_df
