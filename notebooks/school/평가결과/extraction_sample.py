"""추출 정밀도·재현율용 '눈가림' 주석지를 만든다. 프로젝트 루트에서 실행.

    .venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/extraction_sample.py

- 모집단: manifest 선정 쪽수 안의 학교 문서 청크 전부(추출 입력 여부와 무관). 그래야 '추출 대상으로 고르지
  않은 청크'에서 생기는 누락까지 재현율에 잡힌다.
- 층화: 문서(교육부/경기도) × 종류(표 행/본문) 4층 × 3개 = 12개(전 과정 재현율용) + 실제 추출 입력(176개 표 행)에서
  8개(모델 정밀도용 — 무작위 12개 중 추출 입력은 15%뿐이라 정밀도 분모가 거의 0이 된다). seed 고정, 층마다 dev/eval 반씩.
- 모델 추출 결과는 주석지에 넣지 않는다(정답이 모델 출력에 끌려가지 않게). 채점은 extraction_score.py.
"""

import json
import random
import sys

sys.path.insert(0, "src")
from rag.school.capabilities import active_pages, in_scope  # noqa: E402

BUNDLE = "data/interim/school/full_20261105T032224_612625Z/official_chunks.jsonl"
OUT_MD = "notebooks/school/평가결과/추출정답_주석지_v1.md"
OUT_JSON = "data/specs/golden/school_extraction_sample_v1.json"
SEED, PER_STRATUM, FROM_INPUT = 1105, 3, 8
RUN_CHUNKS = "data/interim/school/run_20261105T024843_281371Z/chunks.jsonl"

chunks = [json.loads(line) for line in open(BUNDLE, encoding="utf-8")]
scope = active_pages()
pool = [c for c in chunks if c["document_id"] in ("moe_school_manual_v3", "gyeonggi_school_guide_2025")
        and in_scope(c["document_id"], c["pdf_page"], scope)]
rng = random.Random(SEED)
picked = []
for doc in ("moe_school_manual_v3", "gyeonggi_school_guide_2025"):
    for kind in ("table_row", "text"):
        stratum = sorted((c for c in pool if c["document_id"] == doc and (c["kind"] == "table_row") == (kind == "table_row")),
                         key=lambda c: c["chunk_id"])
        for i, c in enumerate(rng.sample(stratum, PER_STRATUM)):
            picked.append({"chunk_id": c["chunk_id"], "split": "dev" if i % 2 == 0 else "eval", "stratum": f"{doc}/{kind}"})
extracted = sorted({json.loads(line)["official_chunk_id"] for line in open(RUN_CHUNKS, encoding="utf-8")}
                   - {p["chunk_id"] for p in picked})
for i, chunk_id in enumerate(rng.sample(extracted, FROM_INPUT)):
    picked.append({"chunk_id": chunk_id, "split": "dev" if i % 2 == 0 else "eval", "stratum": "extraction_input"})
picked.sort(key=lambda p: (p["split"], p["chunk_id"]))
by_id = {c["chunk_id"]: c for c in chunks}

lines = ["# 추출 정답 주석지 v1 (눈가림)", "",
         f"표본: 선정 쪽수 안 학교 청크 {len(pool)}개에서 문서×종류 4층 무작위 {4 * PER_STRATUM}개 + 추출 입력 표 행 "
         f"{FROM_INPUT}개 = {len(picked)}개 (seed {SEED}). "
         "모델이 뽑은 행동은 일부러 보여 주지 않는다.", "",
         "## 작성 규칙", "",
         "- 원문을 읽고 **학교가 수행해야 하는 행동**을 하나씩 한 행에 적는다. 한 문장에 행동이 둘이면 두 행.",
         "- `행동`: 원문 표현을 살린 짧은 문장. `역할`: 수행 주체(원문에 없으면 비움). "
         "`근거`: 원문이 역할을 정해 둠=EXPLICIT, 예시 표·시나리오 담당=EXAMPLE, 없음=빈칸.",
         "- `단계`: 원문이 배치한 단계(예방/대응1/대응2/대응3/복구/미지정). `조건`: '~시', '~경우' 같은 조건. "
         "`강도`: 반드시/권장/금지/미지정.",
         "- 행동이 없는 청크(증상 설명, 법 조문 인용, 목차 등)는 `행동 없음`에 x 표시만 한다. 이것도 정답이다.",
         "- 수신자(예: '보건교사에게 연락')는 역할이 아니다. 예시 역할을 의무로 바꾸지 않는다.", ""]
for n, p in enumerate(picked, 1):
    c = by_id[p["chunk_id"]]
    lines += [f"## {n}. `{c['chunk_id']}` — {p['split']}",
              "", f"PDF {c['pdf_page']}쪽 (인쇄 {c.get('printed_page') or '-'}쪽) · {c.get('section') or ''}", "",
              "```text", c["text"].replace("<br>", " / "), "```", "",
              "행동 없음: [ ]", "",
              "| # | 행동 | 역할 | 근거 | 단계 | 조건 | 강도 |", "|---|---|---|---|---|---|---|",
              "| 1 |  |  |  |  |  |  |", ""]
open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines))
json.dump({"seed": SEED, "population": len(pool), "bundle": BUNDLE, "sample": picked},
          open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"모집단 {len(pool)} → 표본 {len(picked)} | {OUT_MD}")
