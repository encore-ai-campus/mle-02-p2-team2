"""질문 이력 저장·집계 (유종혁, 11/07). 개발자 대시보드가 쓴다. Streamlit 에 의존하지 않는다.

설명
- 질문마다 한 줄(JSONL)을 `data/interim/ui_history/queries.jsonl` 에 덧붙인다. 이 폴더는 Git 에서 제외된다(`data/interim/`).
- 저장하는 것은 질문 원문, 상태, 의도, 사용한 도구, 건수, 처리 시간이다. 답변 본문·접속 정보·키는 저장하지 않는다.
- 질문 원문에 개인정보가 들어올 수 있으므로 환경 변수 QUERY_LOG=0 이면 저장하지 않는다.
- Streamlit Community Cloud 의 파일 시스템은 앱을 다시 시작하면 지워질 수 있다. 영구 보관이 필요하면 다른 저장소가 필요하다.
- 저장 실패는 화면을 멈추지 않는다(append_record 가 False 를 돌려준다).
"""
from __future__ import annotations

import json
import os
import re
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LOG_PATH = ROOT / "data" / "interim" / "ui_history" / "queries.jsonl"
KST = timezone(timedelta(hours=9))
_LOCK = threading.Lock()   # 같은 프로세스의 동시 요청이 한 줄씩 쓰도록 막는다


def logging_enabled() -> bool:
    """QUERY_LOG=0 이면 이력을 저장하지 않는다. 기본은 저장."""
    return os.getenv("QUERY_LOG", "1") != "0"


def normalize_question(question: str) -> str:
    """같은 질문을 묶기 위한 키. 공백을 하나로 줄이고 앞뒤를 자른다. 대소문자는 구분하지 않는다."""
    return re.sub(r"\s+", " ", question).strip().casefold()


def make_record(question: str, result: dict, *, polish: bool, now: datetime | None = None) -> dict:
    """ask() 결과 한 건을 저장용 한 줄로 줄인다. 오류 결과({"error": ...})도 기록한다."""
    stamp = (now or datetime.now(KST)).isoformat(timespec="seconds")
    base = {"ts": stamp, "question": question, "polish": bool(polish)}
    if "error" in result:
        return {**base, "status": "ERROR", "response_type": None, "intent": None, "tools": [],
                "findings": 0, "citations": 0, "seconds": None, "request_id": None}
    output = result["output"]
    intent = next((t.get("intent") for t in result.get("trace", []) if t.get("step") == "classify_intent"), None)
    return {**base, "status": output["status"], "response_type": output.get("response_type"), "intent": intent,
            "tools": list(output.get("tools_used", [])), "findings": len(output.get("findings", [])),
            "citations": len(output.get("citations", [])), "seconds": result.get("seconds"),
            "request_id": result.get("request_id")}


def append_record(record: dict, path: Path | None = None) -> bool:
    """한 줄을 덧붙인다. 폴더가 없으면 만든다. 실패하면 False (화면은 계속 동작). path 를 비우면 LOG_PATH."""
    path = path or LOG_PATH   # 기본 경로는 호출 때 정한다(시험에서 LOG_PATH 를 바꿀 수 있게)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with _LOCK, path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except OSError:
        return False


def load_records(path: Path | None = None) -> list[dict]:
    """저장된 기록을 읽는다. 파일이 없으면 빈 목록. 깨진 줄은 건너뛴다."""
    path = path or LOG_PATH
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    records = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):   # 객체가 아닌 줄(숫자·목록 등)은 건너뛴다
            records.append(item)
    return records


def csv_safe(frame):
    """CSV 를 스프레드시트로 열 때 수식으로 해석되지 않게, = + - @ 로 시작하는 글자 칸 앞에 작은따옴표를 붙인다."""
    def fix(value):
        if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", chr(9), chr(13)):
            return "'" + value
        return value
    return frame.map(fix)


def summarize(records: list[dict], top: int = 10) -> dict:
    """대시보드용 집계. 질문 빈도는 normalize_question 으로 묶고, 보여 줄 때는 처음 나온 원문을 쓴다."""
    first_text: dict[str, str] = {}
    freq: Counter = Counter()
    for r in records:
        key = normalize_question(r.get("question", ""))
        if not key:
            continue
        first_text.setdefault(key, r["question"].strip())
        freq[key] += 1
    seconds = [r["seconds"] for r in records if isinstance(r.get("seconds"), (int, float))]
    return {
        "total": len(records),
        "top_questions": [(first_text[k], n) for k, n in freq.most_common(top)],
        "by_status": dict(Counter(r.get("status") or "UNKNOWN" for r in records)),
        "by_intent": dict(Counter(r.get("intent") or "없음" for r in records)),
        "by_tool": dict(Counter(t for r in records for t in (r.get("tools") if isinstance(r.get("tools"), list) else []))),
        "by_day": dict(sorted(Counter((r.get("ts") or "")[:10] for r in records if r.get("ts")).items())),
        "avg_seconds": round(sum(seconds) / len(seconds), 2) if seconds else None,
    }
