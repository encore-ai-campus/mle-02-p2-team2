"""추출 정답 주석지 v2 — 현재 청킹(v4.1) 원문으로 v1 표본 20청크를 다시 보여 주고, v1 주석을 옮겨 적는다.

    .venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/extraction_sheet_v2.py --bundle <현재 official_chunks.jsonl>

- 같은 청크 ID를 쓴다(20개 모두 현재 데이터에 있음). 그래야 11/05 결과와 짝지어 비교할 수 있다.
- 청크마다 11/05 원문과 현재 원문을 비교해 '같음 / 서식만 다름 / 내용 다름'을 표시한다. 내용이 다르면 11/05 원문을 접어서 함께 보인다.
- 주석(행동 표·행동 없음)은 v1에서 그대로 옮긴다. 잠정 표시(`?`)도 그대로 둔다. 확정은 작성자가 한다.
- `검토 요청`은 가이드와 맞지 않아 보이는 곳을 적은 것이다(작성: Claude). 정답이 아니며 따를지는 작성자가 정한다.
- 모델 추출 결과는 넣지 않는다(눈가림 유지). 채점은 extraction_score.py --sheet ... --sample ...
"""

import argparse
import json
import re
import sys
from pathlib import Path

OLD_SHEET = "notebooks/school/평가결과/추출정답_주석지_v1.md"
OLD_SAMPLE = "data/specs/golden/school_extraction_sample_v1.json"
OUT_MD = "notebooks/school/평가결과/추출정답_주석지_v2.md"
OUT_JSON = "data/specs/golden/school_extraction_sample_v2.json"
GUIDE = Path(__file__).with_name("추출정답_작성가이드_v1.md")

# 가이드(추출정답_작성가이드_v1.md)와 다르게 보이는 곳. 판정이 아니라 확인 요청이다.
REVIEW = {
    "gyeonggi_school_guide_2025:p0179:t01r07":
        "행동 칸의 '접촉자 조사'는 표의 구분 칸 이름이다. 가이드는 '무엇을 하는지'를 쓰라고 하므로 내용 칸의 "
        "'교내 동선별 접촉자 파악을 위한 학교 도면 파악'이 행동일 수 있다.",
    "gyeonggi_school_guide_2025:p0238:b04":
        "단계 '전체단계?'. 가이드상 '전체단계'는 원문이 모든 단계라고 명시할 때만 쓴다. 휴업 종료 후 정상수업 재개라면 "
        "'복구' 또는 '미지정'일 수 있다. 근거 EXPLICIT은 개별 수행자를 원문이 직접 배정했다는 뜻이므로 유지 여부를 확인.",
    "moe_school_manual_v3:p0038:t01r04":
        "수행자가 교육청이다. 가이드는 '교육청·보건소·의료기관의 내부 업무는 학교 행동에서 제외'한다. 이 기준이면 "
        "'행동 없음 [x]'가 된다.",
    "moe_school_manual_v3:p0066:t03r02":
        "근거 EXPLICIT. 가이드는 교육부 매뉴얼·경기도 가이드북의 역할 배정을 기본적으로 EXAMPLE로 본다(역할표·예시 단서). "
        "다르게 판단했다면 메모를 남겨 주세요.",
    "gyeonggi_school_guide_2025:p0171:t01r04":
        "행동 칸이 '?'라 채점에서 판정 보류로 빠졌다. 원문 '결핵역학조사 대상자들의 학부모에 대한 설명회 시 적극 협조'가 "
        "학교 행동인지 정해 주세요.",
    "moe_school_manual_v3:p0056:t01r02":
        "미작성(행동 없음도 표시 안 됨). '담임교사가 (일시적 관찰실을) 담당할 것을 권고'를 행동으로 볼지, 담당자 배정 "
        "설명이라 행동 없음으로 볼지 정해 주세요.",
    "gyeonggi_school_guide_2025:p0310:b03":
        "**같은 청크 ID지만 원문이 통째로 바뀌었다**(11/05: 행정직원·정보담당교사·회복 조치 → 현재: 보건교사·급식종사자·"
        "돌봄인력). v1의 '행동 없음 [x?]'는 옛 원문 기준이므로 현재 원문으로 다시 주석해 주세요. 현재 원문에는 "
        "'교내 업무 대행자 지정', '상황별 급식 제공 방안 마련'처럼 학교 구성원의 수행 업무가 있다.",
    "moe_school_manual_v3:p0038:b01":
        "현재 원문에 11/05에 없던 '1) 진한이의 증상이 안정될 때까지 … 보호자에게 …' 문장이 들어왔다. v1의 "
        "'행동 없음 [x?]'를 현재 원문으로 다시 확인해 주세요(시나리오 서술은 행동 아님, 지시 문장은 행동일 수 있음).",
    "moe_school_manual_v3:p0066:b01":
        "현재 청크에는 표 캡션([표 3-12]·[표 3-13])이 들어오고 '5) 고위험군의 파악 및 관리' 부분이 빠졌다. v1 판정 "
        "'행동 없음'이 그대로 맞는지만 확인.",
    "gyeonggi_school_guide_2025:p0244:b02":
        "현재 청크는 11/05보다 짧다('나. 밀접 돌봄' 부분이 빠짐). v1 판정(행동 없음 잠정)이 그대로 맞는지만 확인.",
}


def norm(text: str) -> str:
    return re.sub(r"[\s|>*`\-/]", "", re.sub(r"<br\s*/?>", "", text or ""))


def bigram_dice(a: str, b: str) -> float:
    x = {a[i:i + 2] for i in range(len(a) - 1)}
    y = {b[i:i + 2] for i in range(len(b) - 1)}
    return 2 * len(x & y) / (len(x) + len(y)) if x and y else 0.0


def compare(chunk_id: str, old: str, new: str) -> str:
    o, n = norm(old), norm(new)
    if o == n:
        return "같음"
    rows_old = [ln for ln in old.splitlines() if ln.lstrip().startswith("|")]
    rows_new = [ln for ln in new.splitlines() if ln.lstrip().startswith("|")]
    if ":t" in chunk_id and rows_old and norm(" ".join(rows_old)) == norm(" ".join(rows_new)):
        return "표 제목만 다름"       # 행 내용은 같고 위의 표 제목(검수 캡션)만 바뀜 → 주석은 그대로 유효
    return "서식만 다름" if bigram_dice(o, n) >= 0.9 and abs(len(o) - len(n)) <= 6 else "내용 다름"


def old_sections(path: str) -> dict[str, dict]:
    text = Path(path).read_text(encoding="utf-8")
    out = {}
    for part in re.split(r"\n(?=## \d+\. `)", text)[1:]:
        chunk_id = part.split("`")[1]
        original = re.search(r"```text\n(.*?)\n```", part, re.DOTALL).group(1)
        after = part.split("```", 2)[2]
        lines = [ln for ln in after.splitlines()
                 if ln.startswith(("행동 없음:", "|", "검토 메모"))]
        out[chunk_id] = {"original": original, "annotation": lines}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, help="현재 청킹의 official_chunks.jsonl")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if Path(OUT_MD).exists() and not args.force:
        sys.exit(f"{OUT_MD}이 이미 있습니다(작성 중인 주석을 덮어쓰지 않음). 새로 만들려면 --force.")

    chunks = {c["chunk_id"]: c for c in map(json.loads, Path(args.bundle).read_text(encoding="utf-8").splitlines())}
    sample = json.loads(Path(OLD_SAMPLE).read_text(encoding="utf-8"))
    old = old_sections(OLD_SHEET)
    status = {p["chunk_id"]: compare(p["chunk_id"], old[p["chunk_id"]]["original"], chunks[p["chunk_id"]]["text"])
              for p in sample["sample"]}
    counts = {k: list(status.values()).count(k) for k in ("같음", "서식만 다름", "표 제목만 다름", "내용 다름")}

    lines = ["# 추출 정답 주석지 v2 (현재 청킹 v4.1, 눈가림)", "",
             (f"표본: v1과 같은 20청크(seed {sample['seed']}, v1 모집단 {sample['population']}개에서 층화). 원문은 현재 "
             f"적재 청크 글이다. 11/05 원문과 비교: 같음 {counts['같음']} · 서식만 다름 {counts['서식만 다름']} · "
             f"표 제목만 다름 {counts['표 제목만 다름']} · 내용 다름 {counts['내용 다름']}. "
             "모델이 뽑은 행동은 보여 주지 않는다."), "",
             "## v2에서 할 일", "",
             "1. 주석은 v1에서 그대로 옮겼다. **'내용 다름' 청크는 현재 원문 기준으로 주석을 다시 확인**한다.",
             "2. 잠정 표시(`[x?]`, 칸 끝 `?`)를 확정한다. 확정하면 `?`를 지운다(채점은 `?`를 잠정으로 따로 센다).",
             "3. `검토 요청`(Claude 작성)은 가이드와 다르게 보이는 곳이다. 따를지는 작성자가 정하고, 줄은 지워도 된다.",
             ("4. 채점: `.venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/extraction_score.py <결과.md> "
              f"--sheet {OUT_MD} --sample {OUT_JSON}` (현재 공유 DB와 비교)"), "",
             GUIDE.read_text(encoding="utf-8").rstrip(), ""]
    for n, p in enumerate(sample["sample"], 1):
        c, cid = chunks[p["chunk_id"]], p["chunk_id"]
        lines += [f"## {n}. `{cid}` — {p['split']}", "",
                  f"PDF {c['pdf_page']}쪽 (인쇄 {c.get('printed_page') or '-'}쪽) · {c.get('section') or ''}", "",
                  f"**11/05 원문과 비교: {status[cid]}**", "",
                  "```text", c["text"].replace("<br>", " / "), "```", ""]
        if status[cid] == "내용 다름":
            lines += ["<details><summary>11/05 원문(v1 주석의 기준)</summary>", "", "```text", old[cid]["original"],
                      "```", "", "</details>", ""]
        if cid in REVIEW:
            lines += [f"> 검토 요청(Claude): {REVIEW[cid]}", ""]
        lines += [*old[cid]["annotation"], ""]
    Path(OUT_MD).write_text("\n".join(lines), encoding="utf-8")
    Path(OUT_JSON).write_text(json.dumps(
        {"seed": sample["seed"], "population": len(chunks), "bundle": args.bundle,
         "derived_from": OLD_SAMPLE, "note": "v1과 같은 청크 ID. population은 현재 청크 수(표본은 v1 층화 그대로)",
         "sample": sample["sample"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{OUT_MD} | {counts}")


if __name__ == "__main__":
    main()
