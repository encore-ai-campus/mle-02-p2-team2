"""작성한 추출 정답 주석지와 DB에 적재된 SchoolAction을 비교해 정밀도·재현율을 계산한다. 프로젝트 루트에서 실행.

    .venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/extraction_score.py [결과.md]

- 정답 단위: (청크, 행동 1개). 예측: 그 청크에 SUPPORTED_BY로 붙은 적재 행동(일괄 승인 후 = 서비스가 쓰는 데이터).
- 짝짓기: 같은 청크 안에서 행동 문장의 2글자 Dice 유사도가 높은 순으로 1:1 탐욕 매칭(문턱 MATCH_MIN).
  문자열 유사도는 의미 판정이 아니므로 결과 md의 짝 목록을 사람이 훑어보고, 틀린 짝은 OVERRIDES에 적어 고친다.
- 계산(수업 39~44장 score 식): precision = TP/예측 수, recall = TP/정답 수. 빈 분모는 '-'로 적는다.
- 행동 자체와 별도로, 짝지어진 것의 역할·근거(basis)·단계·강도 일치율과 '행동 없음' 청크 판정을 따로 센다.
- 추출 입력(176개 표 행)에 들어갔는지도 표시해, '고르지 않아서 놓친 것'과 '읽고도 놓친 것'을 나눈다.
"""

import collections
import json
import re
import sys

sys.path.insert(0, "src")
from rag.school.graph import default_deps  # noqa: E402

SHEET = "notebooks/school/평가결과/추출정답_주석지_v1.md"
SAMPLE = "data/specs/golden/school_extraction_sample_v1.json"
INPUT_IDS = "data/specs/golden/school_extraction_input_ids.json"   # Git 추적(원 실행 기록은 data/interim, Git 제외)
MATCH_MIN = 0.45
OVERRIDES: dict[str, list[tuple[int, str | None]]] = {}   # {chunk_id: [(정답 행 번호, action_id 또는 None=짝 없음)]}
STAGE = {"예방": "PREVENTION", "대응1": "RESPONSE_1", "대응2": "RESPONSE_2", "대응3": "RESPONSE_3",
         "복구": "RECOVERY", "전체단계": "ALL_STAGES", "국가위기": "NATIONAL_CRISIS", "미지정": "UNSPECIFIED"}
MODALITY = {"반드시": "MANDATORY", "권장": "RECOMMENDED", "재량": "DISCRETIONARY", "미지정": "UNSPECIFIED"}


def bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s\W_]+", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def dice(a: str, b: str) -> float:
    x, y = bigrams(a), bigrams(b)
    return 2 * len(x & y) / (len(x) + len(y)) if x and y else 0.0


def parse_sheet(path: str) -> dict[str, dict]:
    gold, current = {}, None
    for line in open(path, encoding="utf-8"):
        head = re.match(r"## \d+\. `([^`]+)`", line)
        if head:
            current = gold.setdefault(head.group(1), {"none": False, "rows": []})
            continue
        if current is None:
            continue
        if line.startswith("행동 없음:"):
            current["none"] = "[x]" in line.lower().replace(" ", "")
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 7 and cells[0].isdigit() and cells[1]:
            current["rows"].append(dict(zip(("n", "action", "role", "basis", "stage", "when", "modality"), cells)))
    return gold


def fmt(num: int, den: int) -> str:
    return f"{num / den:.2f} ({num}/{den})" if den else "- (0/0)"


def main(out_path: str | None) -> None:
    sample = {s["chunk_id"]: s for s in json.load(open(SAMPLE, encoding="utf-8"))["sample"]}
    gold = parse_sheet(SHEET)
    blank = [c for c in sample if not gold.get(c, {}).get("rows") and not gold.get(c, {}).get("none")]
    if blank:
        print(f"아직 작성하지 않은 청크 {len(blank)}개: {blank}")
    extracted_input = set(json.load(open(INPUT_IDS, encoding="utf-8"))["chunk_ids"])
    deps = default_deps()
    with deps["driver"].session(database=deps.get("database")) as session:
        rows = session.run("""
UNWIND $ids AS id MATCH (c:Chunk {chunk_id: id})<-[:SUPPORTED_BY]-(a:SchoolAction)
OPTIONAL MATCH (a)-[d:DONE_BY]->(r:SchoolRole)
RETURN id AS chunk_id, a.action_id AS action_id, a.action_text AS action_text, a.stage AS stage,
       a.modality AS modality, collect({role: r.name, basis: d.basis}) AS roles""", ids=list(sample)).data()
    deps["driver"].close()
    pred = collections.defaultdict(list)
    for r in rows:
        pred[r["chunk_id"]].append(r)

    tally = collections.defaultdict(lambda: collections.Counter())
    attr = collections.Counter()
    report = ["# 추출 정밀도·재현율 채점 결과", "", f"주석지: `{SHEET}` · 매칭 문턱 Dice ≥ {MATCH_MIN}", ""]
    detail = []
    for chunk_id, info in sample.items():
        if chunk_id in blank:
            continue
        g, p = gold[chunk_id]["rows"], pred.get(chunk_id, [])
        pairs = []
        if chunk_id in OVERRIDES:
            by_id = {a["action_id"]: a for a in p}
            pairs = [(int(n), by_id[a]) for n, a in OVERRIDES[chunk_id] if a]
        else:
            scored = sorted(((dice(gr["action"], pa["action_text"]), int(gr["n"]), pa["action_id"]) for gr in g for pa in p),
                            reverse=True)
            used_g, used_p = set(), set()
            for s, n, a in scored:
                if s >= MATCH_MIN and n not in used_g and a not in used_p:
                    used_g.add(n)
                    used_p.add(a)
                    pairs.append((n, next(x for x in p if x["action_id"] == a)))
        tp = len(pairs)
        kind = "표" if ":t" in chunk_id else "본문"
        group = "추출 입력 표본(모델 성능)" if info["stratum"] == "extraction_input" else "무작위 표본(전 과정)"
        for key in ("전체", group, info["split"], kind, "추출 입력" if chunk_id in extracted_input else "추출 입력 밖"):
            tally[key].update(tp=tp, pred=len(p), gold=len(g))
        if gold[chunk_id]["none"]:
            attr["none_chunks"] += 1
            attr["none_correct"] += not p
        for n, pa in pairs:
            gr = next(x for x in g if int(x["n"]) == n)
            names = [r["role"] for r in pa["roles"] if r["role"]]
            bases = {r["basis"] for r in pa["roles"] if r["role"]}
            attr["pairs"] += 1
            attr["role"] += (gr["role"] or "") in " ".join(names) if gr["role"] else not names
            attr["basis"] += (gr["basis"].upper() in bases) if gr["basis"] else not bases
            attr["stage"] += STAGE.get(gr["stage"], gr["stage"]) == pa["stage"]
            attr["modality"] += MODALITY.get(gr["modality"], gr["modality"]) == pa["modality"]
        missed = [x for x in g if int(x["n"]) not in {n for n, _ in pairs}]
        extra = [x for x in p if x["action_id"] not in {a["action_id"] for _, a in pairs}]
        detail += [f"### `{chunk_id}` ({info['split']}, {kind}, "
                   f"{'추출 입력' if chunk_id in extracted_input else '추출 입력 밖'}) — TP {tp} / 예측 {len(p)} / 정답 {len(g)}", ""]
        detail += [f"- 짝: 정답 {n} “{next(x for x in g if int(x['n']) == n)['action']}” ↔ `{a['action_id']}` “{a['action_text']}”"
                   for n, a in pairs]
        detail += [f"- 놓침(FN): 정답 {x['n']} “{x['action']}”" for x in missed]
        detail += [f"- 더 뽑음(FP): `{x['action_id']}` “{x['action_text']}”" for x in extra]
        detail.append("")

    report += ["| 범위 | precision | recall |", "|---|---|---|"]
    for key in ("전체", "무작위 표본(전 과정)", "추출 입력 표본(모델 성능)", "dev", "eval", "표", "본문", "추출 입력",
                "추출 입력 밖"):
        t = tally.get(key)
        if t:
            report.append(f"| {key} | {fmt(t['tp'], t['pred'])} | {fmt(t['tp'], t['gold'])} |")
    report += ["", "| 짝지어진 행동의 속성 | 일치 |", "|---|---|"]
    for key, label in (("role", "수행 역할"), ("basis", "근거(EXPLICIT/EXAMPLE)"), ("stage", "단계"), ("modality", "강도")):
        report.append(f"| {label} | {fmt(attr[key], attr['pairs'])} |")
    report.append(f"| '행동 없음' 청크에서 아무것도 뽑지 않음 | {fmt(attr['none_correct'], attr['none_chunks'])} |")
    report += ["", "## 청크별 짝·놓침·더 뽑음 (짝이 틀렸으면 OVERRIDES로 고쳐 다시 실행)", "", *detail]
    text = "\n".join(report)
    print(text)
    if out_path:
        open(out_path, "w", encoding="utf-8").write(text)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
