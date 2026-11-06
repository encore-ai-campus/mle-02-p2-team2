"""추출 정밀도·재현율용 '눈가림' 주석지를 만든다. 프로젝트 루트에서 실행.

    .venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/extraction_sample.py --bundle <official_chunks.jsonl>

- 모집단: manifest 선정 쪽수 안의 학교 문서 청크 전부(추출 입력 여부와 무관). 그래야 '추출 대상으로 고르지
  않은 청크'에서 생기는 누락까지 재현율에 잡힌다.
- 층화: 문서(교육부/경기도) × 종류(표 행/본문) 4층 × 3개 = 12개(전 과정 재현율용) + 실제 추출 입력(176개 표 행)에서
  8개(모델 정밀도용 — 무작위 12개 중 추출 입력은 15%뿐이라 정밀도 분모가 거의 0이 된다). seed 고정, 층마다 dev/eval 반씩.
- 모델 추출 결과는 주석지에 넣지 않는다(정답이 모델 출력에 끌려가지 않게). 채점은 extraction_score.py.
"""

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, "src")
from rag.school.capabilities import active_pages, in_scope

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--bundle", required=True, help="검증할 official_chunks.jsonl을 명시")
parser.add_argument("--out-md", default="notebooks/school/평가결과/추출정답_주석지_v1.md")
parser.add_argument("--out-json", default="data/specs/golden/school_extraction_sample_v1.json")
parser.add_argument("--force", action="store_true")
args = parser.parse_args()
BUNDLE = args.bundle
OUT_MD = "notebooks/school/평가결과/추출정답_주석지_v1.md"
OUT_JSON = "data/specs/golden/school_extraction_sample_v1.json"
OUT_MD, OUT_JSON = args.out_md, args.out_json
SEED, PER_STRATUM, FROM_INPUT = 1105, 3, 8
INPUT_IDS = "data/specs/golden/school_extraction_input_ids.json"

if Path(OUT_MD).exists() and not args.force:
    sys.exit(f"{OUT_MD}이 이미 있습니다(작성 중인 주석을 덮어쓰지 않음). 새로 만들려면 --force.")
chunks = [json.loads(line) for line in Path(BUNDLE).read_text(encoding="utf-8").splitlines()]
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
extracted = sorted(set(json.loads(Path(INPUT_IDS).read_text(encoding="utf-8"))["chunk_ids"])
                   & {c["chunk_id"] for c in pool} - {p["chunk_id"] for p in picked})
for i, chunk_id in enumerate(rng.sample(extracted, FROM_INPUT)):
    picked.append({"chunk_id": chunk_id, "split": "dev" if i % 2 == 0 else "eval", "stratum": "extraction_input"})
picked.sort(key=lambda p: (p["split"], p["chunk_id"]))
by_id = {c["chunk_id"]: c for c in chunks}

lines = ["# 추출 정답 주석지 v1 (눈가림)", "",
         (f"표본: 선정 쪽수 안 학교 청크 {len(pool)}개에서 문서×종류 4층 무작위 {4 * PER_STRATUM}개 + 추출 입력 표 행 "
         f"{FROM_INPUT}개 = {len(picked)}개 (seed {SEED}). "
         "모델이 뽑은 행동은 일부러 보여 주지 않는다."), "",
         Path(__file__).with_name("추출정답_작성가이드_v1.md").read_text(encoding="utf-8").rstrip(), ""]
for n, p in enumerate(picked, 1):
    c = by_id[p["chunk_id"]]
    lines += [f"## {n}. `{c['chunk_id']}` — {p['split']}",
              "", f"PDF {c['pdf_page']}쪽 (인쇄 {c.get('printed_page') or '-'}쪽) · {c.get('section') or ''}", "",
              "```text", c["text"].replace("<br>", " / "), "```", "",
              "행동 없음: [ ]", "",
              "| # | 행동 | 역할 | 근거 | 단계 | 조건 | 강도 |", "|---|---|---|---|---|---|---|",
              "| 1 |  |  |  |  |  |  |", ""]
Path(OUT_MD).write_text("\n".join(lines), encoding="utf-8")
Path(OUT_JSON).write_text(json.dumps(
    {"seed": SEED, "population": len(pool), "bundle": BUNDLE, "sample": picked},
    ensure_ascii=False, indent=1), encoding="utf-8")
print(f"모집단 {len(pool)} → 표본 {len(picked)} | {OUT_MD}")
