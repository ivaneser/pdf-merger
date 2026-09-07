#!/usr/bin/env python3
"""
Local PDF Merge Server
-----------------------
A small single-file web app (Python stdlib only) that lets you:
  * upload multiple PDFs through the browser
  * set their merge order by dragging (or using up/down arrows)
  * merge them locally on the server using Ghostscript
  * download the merged PDF

Usage:
    python3 pdf_merge_server.py [port]

Then open http://localhost:<port>
"""

import base64
import os
import shutil
import subprocess
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote

WORKDIR = tempfile.mkdtemp(prefix="pdfmerge_")
GS = shutil.which("gs")

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDF Merger</title>
<style>
  :root { --bg:#0f1220; --card:#1a1f38; --accent:#6c8cff; --text:#e8ebff; --muted:#8b93c7; --ok:#34d399; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         background: radial-gradient(1200px 600px at 50% -10%, #202a52, var(--bg));
         color: var(--text); min-height:100vh; padding:32px 16px; }
  .wrap { max-width: 760px; margin:0 auto; }
  h1 { font-size:24px; margin:0 0 4px; }
  p.sub { color:var(--muted); margin:0 0 24px; }
  .card { background:var(--card); border:1px solid #2a3160; border-radius:14px; padding:20px; margin-bottom:18px; }
  .drop { border:2px dashed #3a4480; border-radius:12px; padding:28px; text-align:center; color:var(--muted);
          transition:.15s; cursor:pointer; }
  .drop.hover { border-color:var(--accent); color:var(--text); background:#1e2550; }
  .drop input { display:none; }
  button { font:inherit; cursor:pointer; border:none; border-radius:10px; padding:10px 16px; }
  .btn-primary { background:var(--accent); color:white; font-weight:600; padding:12px 20px; font-size:15px; }
  .btn-primary:disabled { opacity:.4; cursor:not-allowed; }
  .row { display:flex; gap:10px; align-items:center; }
  .row .grow { flex:1; }
  ol.list { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:8px; }
  li.item { background:#141935; border:1px solid #2a3160; border-radius:10px; padding:10px 12px;
            display:flex; align-items:center; gap:10px; cursor:grab; }
  li.item.dragging { opacity:.4; }
  li.item .handle { color:var(--muted); user-select:none; font-size:16px; letter-spacing:-2px; }
  li.item .name { flex:1; word-break:break-all; font-size:14px; }
  li.item .meta { color:var(--muted); font-size:12px; }
  li.item .min { padding:4px 8px; background:#232a58; color:var(--text); }
  .empty { color:var(--muted); text-align:center; padding:12px; }
  .msg { margin-top:14px; font-size:14px; min-height:20px; }
  .msg.ok { color:var(--ok); }
  .msg.err { color:#ff7a7a; }
  a.dl { display:inline-block; margin-top:14px; background:var(--ok); color:#04231a; font-weight:600;
         padding:12px 20px; border-radius:10px; text-decoration:none; }
  .links { display:flex; gap:10px; align-items:center; }
  .link { color:var(--accent); text-decoration:none; font-size:14px; }
  .num { width:22px; text-align:center; color:var(--muted); font-variant-numeric:tabular-nums; }
</style>
</head>
<body>
<div class="wrap">
  <h1>📄 PDF Merger</h1>
  <p class="sub">Upload PDFs, drag to set the order, then merge — everything runs locally.</p>

  <div class="card">
    <label class="drop" id="drop">
      <div><b>Click to choose</b> or drag &amp; drop PDFs here</div>
      <input type="file" id="file" accept="application/pdf" multiple>
    </label>
  </div>

  <div class="card">
    <div class="row" style="margin-bottom:10px;">
      <strong>Your files (top runs first)</strong>
      <a class="link" href="#" id="clear">clear</a>
    </div>
    <ol class="list" id="list"><div class="empty">No files yet.</div></ol>

    <div style="margin-top:16px;" class="row">
      <button class="btn-primary" id="merge" disabled>Merge PDFs</button>
    </div>
    <div class="msg" id="msg"></div>
    <div id="result"></div>
  </div>
</div>

<script>
const listEl = document.getElementById('list');
const fileInput = document.getElementById('file');
const drop = document.getElementById('drop');
const mergeBtn = document.getElementById('merge');
const clearBtn = document.getElementById('clear');
const msgEl = document.getElementById('msg');
const resultEl = document.getElementById('result');
let files = [];

function fmt(n){ return (n/1048576).toFixed(2)+' MB'; }

function render(){
  if(files.length === 0){
    listEl.innerHTML = '<div class="empty">No files yet.</div>';
    mergeBtn.disabled = true;
    return;
  }
  mergeBtn.disabled = false;
  listEl.innerHTML = '';
  files.forEach((f, i) => {
    const li = document.createElement('li');
    li.className = 'item'; li.draggable = true;
    li.dataset.index = i;
    li.innerHTML = `<span class="handle">⠿</span>
      <span class="num">${i+1}</span>
      <span class="name">${f.name}</span>
      <span class="meta">${fmt(f.size)}</span>
      <span class="min" data-act="up">▲</span>
      <span class="min" data-act="down">▼</span>
      <span class="min" data-act="rm">✕</span>`;
    listEl.appendChild(li);
  });
  bindDrag();
}

function bindDrag(){
  let dragIdx = null;
  document.querySelectorAll('li.item').forEach(li => {
    li.addEventListener('dragstart', () => { dragIdx = +li.dataset.index; li.classList.add('dragging'); });
    li.addEventListener('dragover', e => { e.preventDefault(); });
    li.addEventListener('drop', e => {
      e.preventDefault();
      const idx = +li.dataset.index;
      if(dragIdx === null || dragIdx === idx) return;
      const [moved] = files.splice(dragIdx, 1);
      files.splice(idx, 0, moved);
      render();
    });
    li.addEventListener('dragend', () => { dragIdx = null; li.classList.remove('dragging'); });
  });
  document.querySelectorAll('[data-act]').forEach(el => {
    el.addEventListener('click', e => {
      e.stopPropagation();
      const li = el.closest('li.item');
      const i = +li.dataset.index;
      const act = el.dataset.act;
      if(act === 'rm') { files.splice(i,1); render(); }
      if(act === 'up' && i > 0) { [files[i-1], files[i]] = [files[i], files[i-1]]; render(); }
      if(act === 'down' && i < files.length-1) { [files[i], files[i+1]] = [files[i+1], files[i]]; render(); }
    });
  });
}

function setMsg(text, kind){ msgEl.textContent = text; msgEl.className = 'msg ' + (kind||''); }

async function upload(f){
  const fd = new FormData();
  fd.append('file', f);
  const r = await fetch('upload', { method:'POST', body:fd });
  if(!r.ok) throw new Error('upload failed');
  return await r.json();
}

async function onFiles(fileList){
  setMsg('Uploading…');
  for(const f of fileList){
    const j = await upload(f);
    files.push({ id:j.id, name:f.name, size:f.size });
  }
  render();
  setMsg(`${files.length} file(s) ready. Drag to reorder, then merge.`);
}

drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('hover'); });
drop.addEventListener('dragleave', () => drop.classList.remove('hover'));
drop.addEventListener('drop', e => {
  e.preventDefault(); drop.classList.remove('hover');
  if(e.dataTransfer.files.length) onFiles(e.dataTransfer.files);
});
fileInput.addEventListener('change', () => { if(fileInput.files.length) onFiles(fileInput.files); fileInput.value=''; });
clearBtn.addEventListener('click', e => { e.preventDefault(); files=[]; render(); resultEl.innerHTML=''; setMsg(''); });

mergeBtn.addEventListener('click', async () => {
  mergeBtn.disabled = true; setMsg('Merging locally…', '');
  const names = files.map(f => f.id).join('|');
  try {
    const r = await fetch('merge?files='+encodeURIComponent(names), { method:'GET' });
    const j = await r.json();
    if(!j.ok) throw new Error(j.error || 'merge failed');
    resultEl.innerHTML = `<a class="dl" href="download?files=${encodeURIComponent(names)}">⬇ Download ${j.output}</a>`;
    setMsg(`Merged ${files.length} files → ${j.output}`, 'ok');
  } catch(e){ setMsg(e.message || 'Error', 'err'); }
  finally { mergeBtn.disabled = false; }
});

render();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, content_type="application/json", extra_headers=None):
        if isinstance(body, (dict, list)):
            import json
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass  # quiet

    def do_GET(self):
        from urllib.parse import urlparse
        p = urlparse(self.path)
        if p.path == "/":
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        if p.path == "/merge":
            return self._handle_merge(parse_qs(p.query))
        if p.path == "/download":
            return self._handle_download(parse_qs(p.query))
        self._send(404, {"error": "not found"})

    def do_POST(self):
        from urllib.parse import urlparse
        p = urlparse(self.path)
        if p.path == "/upload":
            return self._handle_upload()
        self._send(404, {"error": "not found"})

    # ---- helpers ----
    def _read_body(self):
        n = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(n) if n else b""

    def _handle_upload(self):
        body = self._read_body()
        # minimal multipart parser
        boundary = self._boundary()
        if boundary is None:
            return self._send(400, {"error": "expected multipart/form-data"})
        parts = body.split(b"--" + boundary)
        fid = None
        fname = None
        data = b""
        for part in parts:
            if not part.strip() or part.strip() in (b"--", b"--\r\n"):
                continue
            if b'filename="' not in part:
                continue
            head, _, content = part.partition(b"\r\n\r\n")
            fname_m = head.split(b'filename="')[1].split(b'"')[0]
            fname = unquote(fname_m.decode())
            # strip trailing CRLF from content
            if content.endswith(b"\r\n"):
                content = content[:-2]
            if content:
                data = content
                fid = str(uuid.uuid4())
                with open(os.path.join(WORKDIR, fid), "wb") as f:
                    f.write(data)
        if not fid:
            return self._send(400, {"error": "no file part"})
        return self._send(200, {"id": fid, "name": fname or "file.pdf"})

    def _boundary(self):
        ct = self.headers.get("Content-Type", "")
        if ct.startswith("multipart/form-data"):
            for kv in ct.split(";"):
                kv = kv.strip()
                if kv.startswith("boundary="):
                    return kv.split("=", 1)[1].strip()
        return None

    def _handle_merge(self, q):
        names = q.get("files", [""])[0]
        ids = [i for i in names.split("|") if i]
        if len(ids) < 1:
            return self._send(400, {"error": "no files"})
        paths = []
        for i in ids:
            p = os.path.join(WORKDIR, i)
            if not os.path.exists(p):
                return self._send(404, {"error": f"missing {i}"})
            paths.append(p)
        out = os.path.join(WORKDIR, "merged_" + uuid.uuid4().hex[:8] + ".pdf")
        cmd = [
            GS, "-q", "-s:Permissions=3",
            "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
            "-dPDFSETTINGS=/prepress",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOUTPUTFILE={out}",
        ] + paths
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=300)
        except FileNotFoundError:
            return self._send(500, {"error": "ghostscript (gs) not found on system"})
        except subprocess.TimeoutExpired:
            return self._send(500, {"error": "merge timed out"})
        if r.returncode != 0:
            return self._send(500, {"error": "gs failed: " + r.stderr.decode(errors="replace")[:500]})
        if not os.path.exists(out):
            return self._send(500, {"error": "output not created"})
        return self._send(200, {"ok": True, "output": os.path.basename(out), "output_id": os.path.basename(out)})

    def _handle_download(self, q):
        names = q.get("files", [""])[0]
        ids = [i for i in names.split("|") if i]
        out_ids = [i for i in ids if "merged_" in i]
        target = os.path.join(WORKDIR, out_ids[-1]) if out_ids else None
        if not target or not os.path.exists(target):
            return self._send(404, {"error": "merged file not found; merge again"})
        size = os.path.getsize(target)
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(target)}"')
        self.end_headers()
        with open(target, "rb") as f:
            shutil.copyfileobj(f, self.wfile)


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    host = "127.0.0.1"
    print(f"PDF Merger running at http://{host}:{port}")
    print(f"Work dir: {WORKDIR}")
    server = ThreadingHTTPServer((host, port), Handler)
    server.serve_forever()
