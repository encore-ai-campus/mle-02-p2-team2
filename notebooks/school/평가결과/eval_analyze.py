import collections
import glob
import json
import sys

S = sys.argv[1]
E = json.load(open(S + "/eval_detail.json", encoding="utf-8"))
G = json.load(open("data/specs/golden/school.json", encoding="utf-8"))
D = sorted(glob.glob("data/interim/school/full_*"))[-1]
CH = {c["chunk_id"]: c for c in map(json.loads, open(D + "/official_chunks.jsonl", encoding="utf-8"))}
EXP = collections.defaultdict(list)
for c in G["citations"]:
    EXP[c["q"]].append(c["chunk_id"])
short = lambda cid: cid.replace("moe_school_manual_v3", "M").replace("gyeonggi_school_guide_2025", "G")

hy = E["rank"]["hybrid"]
print("== 질문 접두사별 hybrid Hit@5")
by = collections.defaultdict(lambda: [0, 0])
for q, r in hy.items():
    k = q[:3]
    by[k][1] += 1
    by[k][0] += bool(r and r <= 5)
for k, v in sorted(by.items()):
    print(k, f"{v[0]}/{v[1]}")

print("== 정답 청크 종류별(질문의 정답 중 하나라도 표 행/문단)")
kind = collections.defaultdict(lambda: [0, 0])
for q, r in hy.items():
    ks = {CH[c]["kind"] for c in EXP[q]}
    key = "문단만" if ks == {"paragraph"} else ("표 행만" if ks == {"table_row"} else "혼합")
    kind[key][1] += 1
    kind[key][0] += bool(r and r <= 5)
for k, v in kind.items():
    print(k, f"{v[0]}/{v[1]}")

print("== 문서별(정답 청크가 한 문서뿐인 질문)")
doc = collections.defaultdict(lambda: [0, 0])
for q, r in hy.items():
    ds = {CH[c]["document_id"][:4] for c in EXP[q]}
    key = ds.pop() if len(ds) == 1 else "두 문서"
    doc[key][1] += 1
    doc[key][0] += bool(r and r <= 5)
for k, v in doc.items():
    print(k, f"{v[0]}/{v[1]}")

print("== 정답 청크 평균 길이(글자) 표 행 vs 문단:",
      round(sum(len(CH[c]["text"]) for q in EXP for c in EXP[q] if CH[c]["kind"] == "table_row") / max(1, sum(1 for q in EXP for c in EXP[q] if CH[c]["kind"] == "table_row"))),
      round(sum(len(CH[c]["text"]) for q in EXP for c in EXP[q] if CH[c]["kind"] == "paragraph") / max(1, sum(1 for q in EXP for c in EXP[q] if CH[c]["kind"] == "paragraph"))))

print("== hybrid Hit@5 실패 질문: 정답 vs 상위 5개")
for q, r in hy.items():
    if r and r <= 5:
        continue
    print(f"{q} rank={r} 정답={[short(c).split(':',1)[0]+':'+short(c).split(':')[1]+':'+short(c).split(':')[2] for c in EXP[q]][:3]}")
    print("     상위5=", [short(c) for c in E['top']['hybrid'][q]])

print("== 실행 결과: 정답 청크 0개 인용 + 인용한 쪽")
for r in E["rows"]:
    if r["hit"] != "-" and r["hit"].startswith("0/"):
        d = E["detail"][r["id"]]
        print(r["id"], "find", r["n_find"], "act", r["n_act"], "인용쪽=", sorted({short(c).split(':')[0][:1] + ":" + short(c).split(':')[1] for c in d["cited"]})[:8])

print("== 상태 불일치")
for r in E["rows"]:
    if not r["ok"]:
        d = E["detail"][r["id"]]
        print(r["id"], r["want"], "->", r["got"], d["warnings"][:2])
print("== Finding 수 상위")
for r in sorted(E["rows"], key=lambda r: -r["n_find"])[:5]:
    print(r["id"], r["n_find"])
