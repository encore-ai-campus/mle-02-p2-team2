"""작성한 추출 정답 주석지와 적재된 SchoolAction을 비교해 정밀도·재현율을 계산한다. 프로젝트 루트에서 실행.

    # 지금 공유 DB의 적재 행동과 비교(기본)
    .venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/extraction_score.py [결과.md]
    # 특정 시점의 적재 패키지와 비교(DB 접속 없음). 예: 11/05 당시 적재본
    .venv/Scripts/python.exe -X utf8 notebooks/school/평가결과/extraction_score.py 결과.md \
        --bundle data/interim/school/full_20261105T032224_612625Z/bundle.json
    # 다른 주석지·표본
    ... --sheet <주석지.md> --sample <표본.json>

- 정답 단위: (청크, 행동 1개). 예측: 그 청크에 SUPPORTED_BY로 붙은 적재 행동(일괄 승인 후 = 서비스가 쓰는 데이터).
- 짝짓기: 같은 청크 안에서 행동 문장의 2글자 Dice 유사도가 높은 순으로 1:1 탐욕 매칭(문턱 MATCH_MIN).
  문자열 유사도는 의미 판정이 아니므로 결과 md의 짝 목록을 사람이 훑어보고, 틀린 짝은 OVERRIDES에 적어 고친다.
- 계산(수업 39~44장 score 식): precision = TP/예측 수, recall = TP/정답 수. 빈 분모는 '-'로 적는다.
- 행동 자체와 별도로, 짝지어진 것의 역할·근거(basis)·단계·강도 일치율과 '행동 없음' 청크 판정을 따로 센다.
- 추출 입력(176개 표 행)에 들어갔는지도 표시해, '고르지 않아서 놓친 것'과 '읽고도 놓친 것'을 나눈다.
- 주석의 '잠정' 표시: `행동 없음: [x?]`는 '행동 없음(잠정)', 칸 끝의 `?`는 그 값(잠정)으로 읽는다. 결과에는
  전체 판정과 '잠정 표시가 없는 청크만'의 판정을 함께 적는다. `[ ]`이면서 행동 행이 없는 청크는 미작성으로 뺀다.
"""

import argparse
import collections
import json
import re
import sys

sys.path.insert(0, "src")

SHEET = "notebooks/school/평가결과/추출정답_주석지_v1.md"
SAMPLE = "data/specs/golden/school_extraction_sample_v1.json"
INPUT_IDS = "data/specs/golden/school_extraction_input_ids.json"   # Git 추적(원 실행 기록은 data/interim, Git 제외)
ROLES = "src/index/school/dictionaries/roles.json"
MATCH_MIN = 0.45
OVERRIDES: dict[str, list[tuple[int, str | None]]] = {}   # {chunk_id: [(정답 행 번호, action_id 또는 None=짝 없음)]}
STAGE = {"예방": "PREVENTION", "대응1": "RESPONSE_1", "대응2": "RESPONSE_2", "대응3": "RESPONSE_3",
         "복구": "RECOVERY", "전체단계": "ALL_STAGES", "국가위기": "NATIONAL_CRISIS", "미지정": "UNSPECIFIED"}
MODALITY = {"반드시": "MANDATORY", "권장": "RECOMMENDED", "재량": "DISCRETIONARY", "미지정": "UNSPECIFIED"}
SCOPES = ("전체", "무작위 표본(전 과정)", "추출 입력 표본(모델 성능)", "dev", "eval", "표", "본문", "추출 입력",
          "추출 입력 밖")


def read_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s\W_]+", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def dice(a: str, b: str) -> float:
    x, y = bigrams(a), bigrams(b)
    return 2 * len(x & y) / (len(x) + len(y)) if x and y else 0.0


def parse_sheet(path: str) -> dict[str, dict]:
    """{chunk_id: {'none', 'tentative', 'undecided', 'rows'}}. 칸 끝 '?'는 떼고 잠정으로, 행동 칸이 '?'뿐이면 판정 보류로 둔다."""
    gold, current = {}, None
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for line in lines:
        head = re.match(r"## \d+\. `([^`]+)`", line)
        if head:
            current = gold.setdefault(head.group(1), {"none": False, "tentative": False, "undecided": False, "rows": []})
            continue
        if current is None:
            continue
        if line.startswith("행동 없음:"):
            mark = line.lower().replace(" ", "")
            current["none"] = "[x" in mark
            current["tentative"] |= current["none"] and "?" in mark
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 7 and cells[0].isdigit() and cells[1]:
            current["tentative"] |= any(c.endswith("?") for c in cells)
            cells = [c.rstrip("?").strip() for c in cells]
            if not cells[1]:                      # 행동 칸이 '?'뿐 = 판정 보류
                current["undecided"] = True
                continue
            current["rows"].append(dict(zip(("n", "action", "role", "basis", "stage", "when", "modality"), cells)))
    return gold


def fmt(num: int, den: int) -> str:
    return f"{num / den:.2f} ({num}/{den})" if den else "- (0/0)"


def f1(tp: int, pred: int, gold: int) -> str:
    p, r = (tp / pred if pred else 0), (tp / gold if gold else 0)
    return f"{2 * p * r / (p + r):.2f}" if p + r else "-"


def role_names() -> dict[str, str]:
    book = read_json(ROLES)
    key = book.get("id_field", "role_id")
    return {e[key]: e.get("name", e[key]) for e in book.get("entries", [])}


def predictions_from_bundle(path: str, ids: list[str]) -> dict[str, list[dict]]:
    """적재 패키지(bundle.json / effective_bundle.json)에서 DB 조회와 같은 모양의 예측을 만든다."""
    bundle = read_json(path)
    actions = {a["action_id"]: a for a in bundle["nodes"]["SchoolAction"]}
    names = role_names()
    roles = collections.defaultdict(list)
    pred = collections.defaultdict(list)
    for e in bundle["edges"]:
        if e["type"] == "DONE_BY":
            roles[e["start_id"]].append({"role": names.get(e["end_id"], e["end_id"]), "basis": e["props"].get("basis")})
    wanted = set(ids)
    for e in bundle["edges"]:
        if e["type"] == "SUPPORTED_BY" and e["end_id"] in wanted and e["start_id"] in actions:
            a = actions[e["start_id"]]
            pred[e["end_id"]].append({"action_id": a["action_id"], "action_text": a["action_text"], "stage": a.get("stage"),
                                      "modality": a.get("modality"), "roles": roles.get(a["action_id"], [])})
    return pred


def predictions_from_db(ids: list[str]) -> dict[str, list[dict]]:
    from rag.school.graph import default_deps

    deps = default_deps()
    with deps["driver"].session(database=deps.get("database")) as session:
        rows = session.run("""
UNWIND $ids AS id MATCH (c:Chunk {chunk_id: id})<-[:SUPPORTED_BY]-(a:SchoolAction)
OPTIONAL MATCH (a)-[d:DONE_BY]->(r:SchoolRole)
RETURN id AS chunk_id, a.action_id AS action_id, a.action_text AS action_text, a.stage AS stage,
       a.modality AS modality, collect({role: r.name, basis: d.basis}) AS roles""", ids=ids).data()
    deps["driver"].close()
    pred = collections.defaultdict(list)
    for r in rows:
        pred[r["chunk_id"]].append(r)
    return pred


def pair_up(chunk_id: str, g: list[dict], p: list[dict]) -> list[tuple[int, dict]]:
    if chunk_id in OVERRIDES:   # 사람이 검토한 짝(--overrides)이 있으면 그것만 쓴다
        by_id = {a["action_id"]: a for a in p}
        return [(int(n), by_id[a]) for n, a in OVERRIDES[chunk_id] if a]
    scored = sorted(((dice(gr["action"], pa["action_text"]), int(gr["n"]), pa["action_id"]) for gr in g for pa in p),
                    reverse=True)
    used_g, used_p, pairs = set(), set(), []
    for s, n, a in scored:
        if s >= MATCH_MIN and n not in used_g and a not in used_p:
            used_g.add(n)
            used_p.add(a)
            pairs.append((n, next(x for x in p if x["action_id"] == a)))
    return pairs


def score_table(sample, gold, pred, extracted_input, chunks) -> tuple[dict, collections.Counter, list[str]]:
    tally = collections.defaultdict(collections.Counter)
    attr = collections.Counter()
    detail = []
    for chunk_id in chunks:
        info = sample[chunk_id]
        g, p = gold[chunk_id]["rows"], pred.get(chunk_id, [])
        pairs = pair_up(chunk_id, g, p)
        tp = len(pairs)
        kind = "표" if ":t" in chunk_id else "본문"
        group = "추출 입력 표본(모델 성능)" if info["stratum"] == "extraction_input" else "무작위 표본(전 과정)"
        where = "추출 입력" if chunk_id in extracted_input else "추출 입력 밖"
        for key in ("전체", group, info["split"], kind, where):
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
        mark = " · **잠정 표시 있음**" if gold[chunk_id]["tentative"] else ""
        label = "행동 없음" if gold[chunk_id]["none"] else f"정답 {len(g)}"
        detail += [f"### `{chunk_id}` ({info['split']}, {kind}, {where}){mark} — TP {tp} / 예측 {len(p)} / {label}", ""]
        detail += [f"- 짝: 정답 {n} “{next(x for x in g if int(x['n']) == n)['action']}” ↔ `{a['action_id']}` “{a['action_text']}”"
                   for n, a in pairs]
        detail += [f"- 놓침(FN): 정답 {x['n']} “{x['action']}”" for x in missed]
        detail += [f"- 더 뽑음(FP): `{x['action_id']}` “{x['action_text']}”" for x in extra]
        detail.append("")
    return tally, attr, detail


def render(title: str, tally, attr) -> list[str]:
    lines = [f"## {title}", "", "| 범위 | precision | recall | F1 |", "|---|---|---|---|"]
    for key in SCOPES:
        t = tally.get(key)
        if t:
            lines.append(f"| {key} | {fmt(t['tp'], t['pred'])} | {fmt(t['tp'], t['gold'])} | {f1(t['tp'], t['pred'], t['gold'])} |")
    lines += ["", "| 짝지어진 행동의 속성 | 일치 |", "|---|---|"]
    for key, label in (("role", "수행 역할"), ("basis", "근거(EXPLICIT/EXAMPLE)"), ("stage", "단계"), ("modality", "강도")):
        lines.append(f"| {label} | {fmt(attr[key], attr['pairs'])} |")
    lines += [f"| '행동 없음' 청크에서 아무것도 뽑지 않음 | {fmt(attr['none_correct'], attr['none_chunks'])} |", ""]
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", nargs="?", help="결과 md 경로(없으면 화면에만)")
    parser.add_argument("--sheet", default=SHEET)
    parser.add_argument("--sample", default=SAMPLE)
    parser.add_argument("--bundle", help="DB 대신 이 적재 패키지의 행동을 예측으로 쓴다")
    parser.add_argument("--overrides", help='검토한 짝 JSON {"pairs": {chunk_id: [[정답 행 번호, action_id 또는 null], ...]}}')
    args = parser.parse_args()
    if args.overrides:
        OVERRIDES.update({k: [tuple(x) for x in v] for k, v in
                          read_json(args.overrides)["pairs"].items()})

    sample = {s["chunk_id"]: s for s in read_json(args.sample)["sample"]}
    gold = parse_sheet(args.sheet)
    blank = [c for c in sample if gold.get(c, {}).get("undecided")
             or (not gold.get(c, {}).get("rows") and not gold.get(c, {}).get("none"))]
    graded = [c for c in sample if c not in blank]
    sure = [c for c in graded if not gold[c]["tentative"]]
    extracted_input = set(read_json(INPUT_IDS)["chunk_ids"])
    pred = predictions_from_bundle(args.bundle, list(sample)) if args.bundle else predictions_from_db(list(sample))

    source = f"적재 패키지 `{args.bundle}`" if args.bundle else "공유 DB(실행 시점)"
    report = ["# 추출 정밀도·재현율 채점 결과", "",
              f"- 주석지: `{args.sheet}` · 표본: `{args.sample}`",
              f"- 예측: {source}",
              (f"- 짝: {'검토한 짝 `' + args.overrides + '` (그 밖의 청크는 Dice)' if args.overrides else '자동'}"
               f" · 매칭 문턱 Dice ≥ {MATCH_MIN}. 채점 청크 {len(graded)}개 / 표본 {len(sample)}개"
               f" (미작성·판정 보류 {len(blank)}개 제외{': ' + ', '.join(f'`{c}`' for c in blank) if blank else ''})"),
              (f"- 잠정 표시(`[x?]`·칸 끝 `?`)가 있는 청크 {len(graded) - len(sure)}개: "
               + (", ".join(f"`{c}`" for c in graded if gold[c]["tentative"]) or "없음")), ""]
    tally, attr, detail = score_table(sample, gold, pred, extracted_input, graded)
    report += render("1. 전체 (잠정 표시는 적힌 값대로)", tally, attr)
    sure_tally, sure_attr, _ = score_table(sample, gold, pred, extracted_input, sure)
    report += render(f"2. 잠정 표시가 없는 청크만 ({len(sure)}개)", sure_tally, sure_attr)
    report += ["## 3. 청크별 짝·놓침·더 뽑음 (짝이 틀렸으면 OVERRIDES로 고쳐 다시 실행)", "", *detail]
    text = "\n".join(report)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
