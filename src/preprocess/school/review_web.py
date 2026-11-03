"""브라우저와 노트북에서 사용하는 로컬 행동 후보 검수 화면."""

import json
import secrets
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


PAGE = '''<!doctype html><html lang="ko"><meta charset="utf-8"><title>학교 행동 후보 검수</title>
<style>body{font:16px system-ui;margin:24px;max-width:1100px;color:#17212b}select,input,textarea,button{font:inherit;padding:9px;margin:5px 0}textarea{width:95%}pre{white-space:pre-wrap;background:#f3f5f7;padding:14px;line-height:1.6}label{display:block}button{cursor:pointer;background:#1454a0;color:white;border:0;border-radius:5px}#message{color:#1454a0;font-weight:bold}</style>
<h1>학교 행동 후보 검수</h1><p>저장된 후보와 원문을 확인하고 판정을 저장하세요. LLM을 다시 호출하지 않습니다.</p>
<label>추출 결과 <select id="run"></select></label><label>행동 후보 <select id="candidate"></select></label>
<div id="detail"></div><label>검수자 <input id="reviewer"></label>
<label>판정 <select id="status"><option value="PENDING">미검수</option><option value="APPROVED">승인</option><option value="NEEDS_REVIEW">확인 필요</option><option value="REJECTED">반려</option></select></label>
<div id="checks"></div><label>행동 수정안 (선택)<textarea id="correction" rows="2"></textarea></label>
<label>판정 메모<textarea id="note" rows="3"></textarea></label>
<button id="save">현재 판정 저장</button> <button id="next">저장 후 다음 후보</button><p id="message"></p>
<script>
const $=id=>document.getElementById(id);let data, dirty=false;
const labels={pdf_source_checked:'PDF 판본·쪽수·근거 확인',role_row_checked:'같은 행의 역할과 행동 확인',example_basis_checked:'예시·명시 여부 확인',stage_condition_checked:'단계·조건·기한·강도 확인',concept_checked:'개념·질병 연결 확인'};
for(const [key,label] of Object.entries(labels)){const el=document.createElement('label');const input=document.createElement('input');input.type='checkbox';input.id=key;el.append(input,document.createTextNode(label));$('checks').append(el)}
function box(title,value){const h=document.createElement('h3'),p=document.createElement('pre');h.textContent=title;p.textContent=typeof value==='string'?value:JSON.stringify(value,null,2);$('detail').append(h,p)}
async function api(path,body){const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const result=await response.json();if(!response.ok)throw Error(result.error);return result}
function show(){const i=Number($('candidate').value),c=data.candidates[i],r=data.reviews[i]||{};const chunk=data.chunks.find(x=>x.chunk_id===c.source_chunk_id)||{};$('detail').replaceChildren();box('行動候補'.replace('行動候補','행동 후보'),c.action_text);box('역할·단계·조건·개념',Object.fromEntries(['roles','stage','step_order','when_text','timing_text','modality','scope','named_diseases','concept_candidate'].map(k=>[k,c[k]])));box('모델의 인용',c.evidence);box('자동 검사 메모',c.check_reasons||[]);box('출처',`${c.document_id} / PDF ${chunk.pdf_page}쪽 / 인쇄 ${chunk.printed_page}쪽\n${chunk.source_path}\n${chunk.section}`);for(const p of chunk.source_parts||[])box('원문: '+p.part,p.text);for(const p of chunk.editorial_context||[])box('표 작업자의 참고 메모',p.text);$('reviewer').value=r.reviewer||$('reviewer').value;$('status').value=r.review_status||'PENDING';$('note').value=r.reason||'';$('correction').value=r.edited_action_text||'';for(const key of Object.keys(labels))$(key).checked=!!r[key];dirty=false;$('message').textContent=`${i+1} / ${data.candidates.length}번째 후보`}
async function load(){data=await api('data?run='+encodeURIComponent($('run').value));$('candidate').replaceChildren();data.candidates.forEach((c,i)=>$('candidate').add(new Option(`${i+1}. ${c.action_text}`,i)));show()}
async function save(next){try{const i=Number($('candidate').value);const review={reviewer:$('reviewer').value,review_status:$('status').value,reason:$('note').value,edited_action_text:$('correction').value,...Object.fromEntries(Object.keys(labels).map(k=>[k,$(k).checked]))};await api('save',{run:$('run').value,index:i,review});data.reviews[i]=review;dirty=false;if(next&&i+1<data.candidates.length){$('candidate').value=i+1;show()}else $('message').textContent='저장했습니다.'}catch(e){$('message').textContent=e.message}}
$('run').onchange=load;$('candidate').onchange=show;$('save').onclick=()=>save(false);$('next').onclick=()=>save(true);document.addEventListener('input',()=>dirty=true);window.onbeforeunload=()=>dirty?'저장하지 않은 판정이 있습니다.':undefined;
api('runs').then(runs=>{if(!runs.length){$('message').textContent='저장된 행동 후보가 없습니다.';return}runs.forEach(r=>$('run').add(new Option(r,r)));return load()}).catch(e=>$('message').textContent=e.message);
</script></html>'''


def start_review_server(base_dir):
    """저장된 로컬 결과만 읽고 쓰는 검수 서버를 시작하고 접속 URL을 반환한다."""
    base = Path(base_dir).resolve()
    folders = {}
    for path in sorted(base.glob("*/run.jsonl"), reverse=True):
        runs = read_rows(path)
        if runs and runs[0].get("status") == "SUCCESS" and read_rows(path.parent / "steps_raw.jsonl"):
            folders[path.parent.name] = path.parent
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status, value, html=False):
            body = value.encode("utf-8") if html else json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            from urllib.parse import parse_qs, urlsplit
            url = urlsplit(self.path)
            prefix = f"/{token}/"
            if not url.path.startswith(prefix):
                return self.respond(404, {"error": "화면 주소를 확인하세요."})
            route = url.path[len(prefix):]
            if not route:
                return self.respond(200, PAGE, html=True)
            if route == "runs":
                return self.respond(200, list(folders))
            name = parse_qs(url.query).get("run", [""])[0]
            if route != "data" or name not in folders:
                return self.respond(404, {"error": "추출 결과를 찾지 못했습니다."})
            folder = folders[name]
            candidates = read_rows(folder / "steps_raw.jsonl")
            existing = read_rows(folder / "review.jsonl") if (folder / "review.jsonl").exists() else []
            reviews = [next((r for r in existing if r.get("candidate_index") == i+1 and r.get("source_chunk_id") == c["source_chunk_id"]), {}) for i, c in enumerate(candidates)]
            self.respond(200, {"candidates": candidates, "reviews": reviews, "chunks": read_rows(folder / "chunks.jsonl")})

        def do_POST(self):
            if self.path != f"/{token}/save":
                return self.respond(404, {"error": "잘못된 저장 경로"})
            try:
                payload = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 100_000)))
                folder = folders[payload["run"]]
                candidates = read_rows(folder / "steps_raw.jsonl")
                index = payload["index"]
                if not isinstance(index, int) or not 0 <= index < len(candidates):
                    raise ValueError("후보 번호를 확인하세요.")
                candidate = candidates[index]
                review = payload["review"]
                status = review.get("review_status")
                keys = ("pdf_source_checked", "role_row_checked", "example_basis_checked", "stage_condition_checked", "concept_checked")
                if status not in ("PENDING", "APPROVED", "NEEDS_REVIEW", "REJECTED"):
                    raise ValueError("판정을 확인하세요.")
                if not review.get("reviewer", "").strip():
                    raise ValueError("검수자 이름을 입력하세요.")
                if status == "APPROVED" and not all(review.get(k) is True for k in keys):
                    raise ValueError("승인하려면 다섯 항목을 확인하세요. 미확인 사항은 ‘확인 필요’로 저장할 수 있습니다.")
                if status in ("NEEDS_REVIEW", "REJECTED") and not review.get("reason", "").strip():
                    raise ValueError("판정 메모를 입력하세요.")
                rows = read_rows(folder / "review.jsonl") if (folder / "review.jsonl").exists() else []
                rows = [r for r in rows if not (r.get("candidate_index") == index+1 and r.get("source_chunk_id") == candidate["source_chunk_id"])]
                rows.append({**review, "document_id": candidate["document_id"], "source_chunk_id": candidate["source_chunk_id"], "candidate_index": index+1, "reviewed_at": datetime.now(UTC).isoformat()})
                target = folder / "review.jsonl"
                temporary = target.with_suffix(".jsonl.tmp")
                temporary.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
                temporary.replace(target)
                self.respond(200, {"saved": True})
            except (KeyError, ValueError, TypeError) as exc:
                self.respond(400, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/{token}/"
