"""Federal Filings — Tie-Out Server
Upload a filing HTML → mechanical scan → Claude judgment pass → side-by-side
workbook (.xlsx) + tie-out memo (.md), identical in format to the manually
produced deliverables.

Run:  export ANTHROPIC_API_KEY=sk-ant-...   (get one at console.anthropic.com)
      pip install flask anthropic openpyxl
      python app.py                          # serves on http://0.0.0.0:8788
"""
import datetime
import os
import sys
import tempfile
import traceback
import uuid

from flask import Flask, request, send_file, jsonify

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tieout_scan import text_from_html, find_section, extract_numbers, classify, \
    DEFAULT_NARRATIVE_START, DEFAULT_NARRATIVE_END, DEFAULT_FS_START, DEFAULT_FS_END
from ai_pass import run_ai_pass
from workbook import build_workbook

app = Flask(__name__)
JOBS = {}  # id -> {xlsx, memo, name}
WORK = tempfile.mkdtemp(prefix="tieout_")

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Federal Filings — Tie-Out</title>
<style>
body{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:#f5f7fa;color:#1a2433;margin:0}
header{background:#0f2647;color:#fff;padding:18px 32px;display:flex;gap:14px;align-items:baseline}
header .b{color:#c9a54a;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;font-size:15px}
main{max-width:760px;margin:40px auto;padding:0 20px}
#drop{border:2px dashed #b8c4d4;border-radius:10px;background:#fff;padding:52px 20px;text-align:center;cursor:pointer}
#drop.over{border-color:#c9a54a;background:#fdf9ef}
#status{margin:20px 0;font-size:14px;color:#5b6b7f;white-space:pre-line}
.links a{display:inline-block;background:#0f2647;color:#fff;border-radius:7px;padding:11px 20px;text-decoration:none;margin-right:10px;font-size:14px}
.note{background:#eef4fb;border:1px solid #cfe0f4;border-radius:8px;padding:12px 16px;font-size:13px;color:#274a75;margin-top:22px;line-height:1.55}
</style></head><body>
<header><span class="b">Federal Filings</span><h1 style="font-size:20px;margin:0">Filing Tie-Out — AI Review</h1></header>
<main>
<p style="color:#5b6b7f;font-size:13.5px">Upload the filing HTML. The server runs the mechanical number scan, then Claude performs the full
judgment pass — caption matching, correct F/S values on mismatches, year-label checks, recomputed percentages — and returns the
side-by-side Excel workbook and tie-out memo. A full review takes 2–5 minutes.</p>
<div id="pc" style="margin-bottom:14px;display:none">Team passcode: <input type="password" id="passcode" style="padding:7px 10px;border:1px solid #c7d0dc;border-radius:6px"></div>
<div id="drop"><strong>Drop the filing here</strong> or click to browse<br><span style="color:#5b6b7f;font-size:13px">10-K and 10-Q auto-detected</span>
<input type="file" id="f" accept=".htm,.html" style="display:none"></div>
<div id="status"></div><div class="links" id="links"></div>
<div class="note">Filings are processed on this server and sent to the Anthropic API for the judgment pass. Do not use for filings
that cannot leave the building without that being acceptable. The workbook statuses still deserve a preparer's read-through before
anything goes to a client.</div>
</main>
<script>
const drop=document.getElementById('drop'),f=document.getElementById('f'),st=document.getElementById('status'),lk=document.getElementById('links');
fetch('/config').then(r=>r.json()).then(c=>{if(c.passcode_required)document.getElementById('pc').style.display='block'});
drop.onclick=()=>f.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add('over')};
drop.ondragleave=()=>drop.classList.remove('over');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('over');if(e.dataTransfer.files[0])go(e.dataTransfer.files[0])};
f.onchange=()=>{if(f.files[0])go(f.files[0])};
async function go(file){
  lk.innerHTML='';st.textContent='Uploading '+file.name+' …\\nRunning mechanical scan + AI review — takes a few minutes. Leave this tab open.';
  const fd=new FormData();fd.append('filing',file);fd.append('passcode',document.getElementById('passcode').value);
  try{
    const res=await fetch('/analyze',{method:'POST',body:fd});
    const j=await res.json();
    if(j.error){st.textContent='Error: '+j.error;return}
    st.textContent=file.name+' — review complete. '+j.summary;
    lk.innerHTML='<a href="/download/'+j.id+'/xlsx">Download side-by-side Excel</a><a href="/download/'+j.id+'/memo">Download tie-out memo</a>';
  }catch(e){st.textContent='Error: '+e.message}
}
</script></body></html>"""


@app.route("/")
def index():
    return PAGE


@app.route("/config")
def config():
    return jsonify(passcode_required=bool(os.environ.get("TIEOUT_PASSCODE")))


@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        pc = os.environ.get("TIEOUT_PASSCODE")
        if pc and request.form.get("passcode") != pc:
            return jsonify(error="Wrong or missing team passcode")
        up = request.files["filing"]
        raw = up.read().decode("utf-8", errors="ignore")
        text = text_from_html(raw)
        n_start, n_end = find_section(text, DEFAULT_NARRATIVE_START, DEFAULT_NARRATIVE_END,
                                      min_pos=len(text) // 20)
        if n_start is None:
            return jsonify(error="Could not locate the MD&A section")
        f_start, f_end = find_section(text, DEFAULT_FS_START, DEFAULT_FS_END,
                                      min_pos=len(text) // 50)
        if f_start is None:
            return jsonify(error="Could not locate the financial statements")
        if f_start < n_start:
            f_end = min(f_end, n_start)
        narrative, fs = text[n_start:n_end], text[f_start:f_end]
        narrative_nums = extract_numbers(narrative, n_start, 1000)
        fs_nums = extract_numbers(fs, f_start, 0)
        results = classify(narrative_nums, fs_nums)
        scan = {
            "flags": [r for r in results if r["status"] == "FLAG"],
            "derived": [r for r in results if r["status"].startswith("DERIVED")],
            "matched": [r for r in results if r["status"] == "MATCH"],
        }
        ai = run_ai_pass(narrative, fs, scan)

        job = uuid.uuid4().hex[:12]
        base = os.path.join(WORK, job)
        today = datetime.date.today().strftime("%m/%d/%Y")
        title = f"{ai.get('company','')} — {ai.get('form','')} ({ai.get('period','')}) · MD&A vs. Financial Statements — Side-by-Side Tie-Out"
        subtitle = (f"Source: {up.filename} · Prepared {today} by Federal Filings Tie-Out Server · "
                    "Red = mismatch/wrong year, amber = no F/S support (confirm), green = ties.")
        build_workbook(title, subtitle, ai["rows"], base + ".xlsx")
        with open(base + ".md", "w", encoding="utf-8") as f:
            f.write(ai["memo_markdown"])
        JOBS[job] = {"xlsx": base + ".xlsx", "memo": base + ".md", "name": up.filename}
        n_bad = sum(1 for r in ai["rows"] if r["status"] in ("MISMATCH", "YEAR LABEL ERROR"))
        n_ns = sum(1 for r in ai["rows"] if r["status"].startswith("NOT IN"))
        return jsonify(id=job, summary=f"{len(ai['rows'])} figures compared: "
                       f"{n_bad} mismatches, {n_ns} unsupported, "
                       f"{len(ai['rows']) - n_bad - n_ns} tie.")
    except Exception as e:
        traceback.print_exc()
        return jsonify(error=str(e))


@app.route("/download/<job>/<kind>")
def download(job, kind):
    j = JOBS.get(job)
    if not j:
        return "expired", 404
    stem = os.path.splitext(j["name"])[0]
    if kind == "xlsx":
        return send_file(j["xlsx"], as_attachment=True, download_name=stem + "_SideBySide_TieOut.xlsx")
    return send_file(j["memo"], as_attachment=True, download_name=stem + "_TieOut_Memo.md")


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("TIEOUT_MOCK"):
        print("WARNING: ANTHROPIC_API_KEY is not set — /analyze will fail.")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8788)))
