"""VIDE IT ai - Simple RAG Backend"""
import os, json, uuid, shutil, hashlib, secrets
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import chromadb
from sentence_transformers import SentenceTransformer

# Paths
ROOT = Path.home() / ".vide-it-ai"
DOCS = ROOT / "documents"
CHROMA = ROOT / "chromadb"
DB_PATH = CHROMA / "db"
USERS_FILE = ROOT / "users.json"

for p in [DOCS, CHROMA, DB_PATH]:
    p.mkdir(parents=True, exist_ok=True)

# Auth helpers
def _hash(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def _load_users():
    if not USERS_FILE.exists():
        return {}
    try:
        return json.loads(USERS_FILE.read_text())
    except Exception:
        return {}

def _save_users(u):
    USERS_FILE.write_text(json.dumps(u, indent=2))

def ensure_admin():
    users = _load_users()
    if "admin" not in users or not users["admin"].get("pw"):
        users["admin"] = {
            "username": "admin",
            "pw": _hash("admin"),
            "role": "admin",
            "active": True,
            "created_at": datetime.utcnow().isoformat(),
        }
        _save_users(users)
    return users

def get_user(request):
    sid = request.cookies.get("vide_session")
    if not sid:
        return None
    users = _load_users()
    for u in users.values():
        if u.get("session") == sid and u.get("active", True):
            return u
    return None

def require_user(request):
    u = get_user(request)
    if not u:
        raise HTTPException(status_code=302, headers={"Location": "/login"}, detail="Login required")
    return u

# ChromaDB
client = chromadb.PersistentClient(path=str(DB_PATH))
collection = client.get_or_create_collection("vide_knowledge", metadata={"hnsw:space": "cosine"})

# Embeddings
embedder = SentenceTransformer("all-MiniLM-L6-v2")

# Ollama
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("VIDE_MODEL", "vide-ai")

app = FastAPI(title="VIDE IT ai")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Query(BaseModel):
    question: str
    top_k: int = 4

# =================== AUTH PAGES ===================

LOGIN_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>VIDE IT ai - Login</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: linear-gradient(135deg, #4f46e5, #7c3aed); min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .card { background: white; padding: 32px; border-radius: 16px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.25); width: 100%; max-width: 360px; }
        h1 { font-size: 24px; margin-bottom: 8px; color: #1e293b; }
        p { font-size: 14px; color: #64748b; margin-bottom: 24px; }
        label { display: block; font-size: 13px; font-weight: 500; color: #475569; margin-bottom: 6px; }
        input[type="text"], input[type="password"] { width: 100%; padding: 10px 12px; border: 1px solid #e2e8f0; border-radius: 8px; font-size: 14px; margin-bottom: 16px; }
        button[type="submit"] { width: 100%; padding: 10px; background: #4f46e5; color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; }
        button[type="submit"]:hover { background: #4338ca; }
        .error { background: #fee2e2; color: #991b1b; padding: 10px; border-radius: 8px; margin-bottom: 16px; font-size: 13px; }
        .hint { font-size: 11px; color: #94a3b8; margin-top: 16px; text-align: center; }
    </style>
</head>
<body>
    <div class="card">
        <h1>VIDE IT ai</h1>
        <p>Login to the management portal</p>
        <div class="error">Invalid credentials</div>
        <form method="post" action="/login">
            <label>Username</label>
            <input type="text" name="username" required autofocus>
            <label>Password</label>
            <input type="password" name="password" required>
            <input type="hidden" name="next" value="/">
            <button type="submit">Sign in</button>
        </form>
        <p class="hint">Default credentials: admin / admin</p>
    </div>
</body>
</html>"""

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    return HTMLResponse(LOGIN_HTML.replace("Invalid credentials", ""))

@app.post("/login")
async def do_login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    users = ensure_admin()
    u = users.get(username)
    if not u or u.get("pw") != _hash(password) or not u.get("active", True):
        return HTMLResponse(LOGIN_HTML, status_code=401)
    sid = secrets.token_hex(16)
    u = dict(u)
    u["session"] = sid
    users[username] = u
    _save_users(users)
    resp = RedirectResponse(next if next.startswith("/") else "/", status_code=303)
    resp.set_cookie("vide_session", sid, httponly=True)
    return resp

@app.post("/logout")
async def do_logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("vide_session")
    return resp

@app.get("/logout")
async def logout_get():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("vide_session")
    return resp

# =================== DASHBOARD ===================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    
    is_admin = user.get("role") == "admin"
    
    header_gradient = "linear-gradient(135deg, #4f46e5, #7c3aed)" if is_admin else "linear-gradient(135deg, #1e293b, #0f172a)"
    title_size = "26px" if is_admin else "22px"
    
    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>VIDE IT ai - Dashboard</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #f8fafc; }}
        .header {{ background: {header_gradient}; color: white; padding: 20px; }}
        .header-content {{ max-width: 1400px; margin: 0 auto; display: flex; justify-content: space-between; align-items: center; }}
        .header h1 {{ font-size: {title_size}; }}
        .header p {{ opacity: 0.85; font-size: 13px; margin-top: 4px; }}
        .stats {{ background: white; border-bottom: 1px solid #e2e8f0; padding: 16px 0; }}
        .stats-content {{ max-width: 1400px; margin: 0 auto; padding: 0 20px; display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; }}
        .stat {{ text-align: center; }}
        .stat-label {{ font-size: 11px; color: #64748b; text-transform: uppercase; font-weight: 600; }}
        .stat-value {{ font-size: 28px; font-weight: 700; color: #1e293b; margin-top: 4px; }}
        .container {{ max-width: 1400px; margin: 0 auto; padding: 24px 20px; display: grid; grid-template-columns: 1fr 380px; gap: 20px; }}
        @media (max-width: 900px) {{ .container {{ grid-template-columns: 1fr; }} }}
        .card {{ background: white; border-radius: 12px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border: 1px solid #e2e8f0; }}
        .card h2 {{ font-size: 16px; color: #1e293b; margin-bottom: 14px; }}
        .upload-zone {{ border: 2px dashed #cbd5e1; border-radius: 10px; padding: 24px; text-align: center; background: #fafafa; cursor: pointer; transition: all 0.2s; }}
        .upload-zone:hover {{ border-color: #4f46e5; background: #f5f3ff; }}
        .upload-zone.dragover {{ border-color: #4f46e5; background: #eef2ff; }}
        .btn {{ background: #4f46e5; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; }}
        .btn:hover {{ background: #4338ca; }}
        .btn-secondary {{ background: white; color: #475569; border: 1px solid #e2e8f0; }}
        .btn-secondary:hover {{ background: #f8fafc; }}
        .btn-danger {{ background: #ef4444; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; }}
        .btn-danger:hover {{ background: #dc2626; }}
        .btn-sm {{ padding: 4px 10px; font-size: 12px; }}
        input, textarea, select {{ width: 100%; padding: 8px 10px; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 13px; margin: 6px 0; }}
        textarea {{ min-height: 90px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
        .chat-window {{ background: #f8fafc; border-radius: 10px; padding: 12px; min-height: 280px; max-height: 420px; overflow-y: auto; border: 1px solid #e2e8f0; }}
        .chat-bubble {{ max-width: 85%; padding: 10px 14px; border-radius: 14px; margin: 8px 0; font-size: 13px; line-height: 1.5; }}
        .chat-bubble.user {{ background: #4f46e5; color: white; margin-left: auto; border-bottom-right-radius: 4px; }}
        .chat-bubble.assistant {{ background: white; border: 1px solid #e2e8f0; margin-right: auto; border-bottom-left-radius: 4px; }}
        .chat-bubble.system {{ background: #fef3c7; color: #92400e; text-align: center; margin: 8px auto; font-size: 12px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
        th {{ background: #f8fafc; font-weight: 600; color: #475569; font-size: 12px; text-transform: uppercase; padding: 10px; border-bottom: 1px solid #e2e8f0; }}
        td {{ padding: 10px; border-bottom: 1px solid #f1f5f9; font-size: 13px; }}
        .msg {{ padding: 10px 12px; border-radius: 8px; margin: 8px 0; font-size: 13px; }}
        .msg.success {{ background: #dcfce7; color: #166534; }}
        .msg.error {{ background: #fee2e2; color: #991b1b; }}
        .msg.info {{ background: #e0f2fe; color: #075985; }}
        .user-card {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; margin: 8px 0; display: flex; justify-content: space-between; align-items: center; }}
        .user-info {{ flex: 1; }}
        .user-name {{ font-weight: 600; color: #1e293b; }}
        .user-role {{ font-size: 12px; color: #64748b; }}
        .user-actions {{ display: flex; gap: 6px; }}
        .badge {{ display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; background: #eef2ff; color: #4f46e5; border: 1px solid #c7d2fe; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-content">
            <div>
                <h1>VIDE IT ai</h1>
                <p>Document knowledge base and AI assistant</p>
            </div>
            <div style="display: flex; align-items: center; gap: 12px;">
                <span class="badge" id="modelBadge">vide-ai · 64k ctx</span>
                <form method="post" action="/logout" style="display: inline;">
                    <button class="btn btn-sm" type="submit">Logout</button>
                </form>
            </div>
        </div>
    </div>
    
    <div class="stats">
        <div class="stats-content">
            <div class="stat">
                <div class="stat-label">Documents</div>
                <div class="stat-value" id="statDocs">0</div>
            </div>
            <div class="stat">
                <div class="stat-label">Total Chunks</div>
                <div class="stat-value" id="statChunks">0</div>
            </div>
            <div class="stat">
                <div class="stat-label">Model</div>
                <div class="stat-value" style="font-size: 22px;" id="statModel">vide-ai</div>
            </div>
        </div>
    </div>
    
    <div class="container">
        <div style="display: flex; flex-direction: column; gap: 20px;">
            <!-- Upload Section -->
            <div class="card">
                <h2>Upload Documents</h2>
                <div class="upload-zone" id="dropZone">
                    <p><strong>Drag & drop files here</strong></p>
                    <p style="font-size: 12px; color: #64748b; margin-top: 4px;">or click to browse</p>
                    <p style="font-size: 11px; color: #94a3b8; margin-top: 8px;">PDF · DOCX · TXT · MD · PPT · XLS · CSV · HTML · CODE · LOG</p>
                    <input type="file" id="fileInput" style="display: none;" multiple accept=".pdf,.docx,.doc,.txt,.md,.ppt,.pptx,.xls,.xlsx,.csv,.html,.htm,.xml,.log,.py,.js,.json,.yaml,.yml">
                </div>
                <div style="margin-top: 12px;">
                    <input type="text" id="uploadTitle" placeholder="Title (optional, defaults to filename)">
                    <select id="topK" style="width: 200px;">
                        <option value="4">4 chunks (default)</option>
                        <option value="6">6 chunks</option>
                        <option value="8">8 chunks</option>
                    </select>
                    <button class="btn" id="uploadBtn" disabled style="margin-left: 8px;">Upload</button>
                </div>
                <div id="uploadMsg"></div>
            </div>
            
            <!-- Chat Section -->
            <div class="card">
                <h2>Chat with VIDE IT ai</h2>
                <div class="chat-window" id="chatWindow">
                    <div class="chat-bubble system">Welcome! Upload documents and ask questions. Context will be retrieved from your knowledge base.</div>
                </div>
                <div style="display: flex; gap: 8px; margin-top: 10px;">
                    <textarea id="question" rows="2" placeholder="Ask about your documents..."></textarea>
                    <button class="btn" id="askBtn" style="align-self: flex-end;">Ask</button>
                </div>
                <div id="answer"></div>
            </div>
            
            <!-- Documents Section -->
            <div class="card">
                <h2>Knowledge Base</h2>
                <div style="display: flex; gap: 8px; margin-bottom: 12px;">
                    <input type="text" id="searchBox" placeholder="Search documents..." style="width: 240px;">
                    <button class="btn btn-sm" onclick="listDocuments()">Refresh</button>
                    <button class="btn btn-danger btn-sm" onclick="clearAll()">Clear All</button>
                </div>
                <div style="overflow-x: auto;">
                    <table>
                        <thead>
                            <tr><th>Title</th><th>File</th><th>Size</th><th>Added</th><th></th></tr>
                        </thead>
                        <tbody id="docsTable"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <script>
        const API = window.location.origin;
        const dropZone = document.getElementById('dropZone');
        const fileInput = document.getElementById('fileInput');
        const uploadBtn = document.getElementById('uploadBtn');
        
        dropZone.onclick = () => fileInput.click();
        dropZone.ondragover = (e) => {{ e.preventDefault(); dropZone.classList.add('dragover'); }};
        dropZone.ondragleave = () => dropZone.classList.remove('dragover');
        dropZone.ondrop = (e) => {{ e.preventDefault(); dropZone.classList.remove('dragover'); fileInput.files = e.dataTransfer.files; syncUploadBtn(); }};
        fileInput.onchange = () => syncUploadBtn();
        
        function syncUploadBtn() {{
            uploadBtn.disabled = !fileInput.files.length;
        }}
        
        uploadBtn.onclick = async () => {{
            const title = document.getElementById('uploadTitle').value;
            const files = Array.from(fileInput.files);
            const msg = document.getElementById('uploadMsg');
            msg.innerHTML = '<div class="msg info">Uploading... Please wait.</div>';
            uploadBtn.disabled = true;
            
            let done = 0, failed = 0;
            for (const file of files) {{
                const fd = new FormData();
                fd.append('file', file);
                fd.append('title', title || file.name);
                try {{
                    const r = await fetch(API + '/documents/upload', {{ method: 'POST', body: fd }});
                    const data = await r.json();
                    if (data.status === 'ok') done++;
                    else {{ failed++; msg.innerHTML += `<div class="msg error">${{file.name}}: ${{data.message}}</div>`; }}
                }} catch(err) {{ failed++; msg.innerHTML += `<div class="msg error">${{file.name}}: ${{err.message}}</div>`; }}
            }}
            
            msg.innerHTML += `<div class="msg success">Uploaded ${{done}}/${{files.length}} files</div>`;
            uploadBtn.disabled = false;
            fileInput.value = '';
            listDocuments();
            refreshStats();
        }};
        
        document.getElementById('askBtn').onclick = async () => {{
            const q = document.getElementById('question').value.trim();
            if (!q) return;
            const chat = document.getElementById('chatWindow');
            chat.innerHTML += `<div class="chat-bubble user">${{q}}</div>`;
            document.getElementById('question').value = '';
            
            try {{
                const r = await fetch(API + '/query', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{ question: q, top_k: parseInt(document.getElementById('topK').value) || 4 }})
                }});
                const data = await r.json();
                chat.innerHTML += `<div class="chat-bubble assistant"><strong>VIDE IT ai:</strong><br>${{data.answer}}</div>`;
                if (data.sources && data.sources.length) {{
                    chat.innerHTML += `<div class="chat-bubble system">Sources: ${{data.sources.map(s => s.title || s.filename).join(', ')}}</div>`;
                }}
            }} catch(err) {{
                chat.innerHTML += `<div class="chat-bubble system">Error: ${{err.message}}</div>`;
            }}
            chat.scrollTop = chat.scrollHeight;
        }};
        
        async function listDocuments() {{
            const tb = document.getElementById('docsTable');
            const search = (document.getElementById('searchBox').value || '').toLowerCase();
            try {{
                const r = await fetch(API + '/documents');
                const data = await r.json();
                const docs = (data.documents || []).filter(d => !search || ((d.title || '') + ' ' + (d.filename || '')).toLowerCase().includes(search));
                if (!docs.length) {{ tb.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#64748b;">No documents found</td></tr>'; return; }}
                tb.innerHTML = docs.map(d => `<tr>
                    <td><strong>${{d.title || '—'}}</strong></td>
                    <td><code>${{d.filename || ''}}</code></td>
                    <td>${{formatSize(d.size)}}</td>
                    <td>${{d.ext || '—'}}</td>
                    <td><button class="btn btn-danger btn-sm" onclick="deleteDoc('${{d.doc_id}}')">Delete</button></td>
                </tr>`).join('');
            }} catch(e) {{ tb.innerHTML = '<tr><td colspan="5" class="msg error">Failed to load</td></tr>'; }}
        }}
        
        function formatSize(bytes) {{
            if (!bytes) return '—';
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / 1048576).toFixed(1) + ' MB';
        }}
        
        async function deleteDoc(id) {{
            if (!confirm('Delete this document?')) return;
            await fetch(API + '/documents/' + id, {{ method: 'DELETE' }});
            listDocuments();
        }}
        
        async function clearAll() {{
            if (!confirm('Delete ALL documents and chunks?')) return;
            await fetch(API + '/documents', {{ method: 'DELETE' }});
            listDocuments();
        }}
        
        async function refreshStats() {{
            try {{
                const r = await fetch(API + '/health');
                const data = await r.json();
                document.getElementById('statModel').textContent = data.model || 'vide-ai';
                document.getElementById('statDocs').textContent = data.documents_indexed || 0;
                document.getElementById('statChunks').textContent = data.documents_indexed || 0;
            }} catch(e) {{}}
        }}
        
        refreshStats();
        listDocuments();
        setInterval(refreshStats, 5000);
        setInterval(listDocuments, 30000);
    </script>
</body>
</html>""")

# =================== API ROUTES ===================

@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL, "documents_indexed": collection.count()}

@app.post("/documents/upload")
async def upload(request: Request, file: UploadFile = File(...), title: str = Form("")):
    require_user(request)
    doc_id = str(uuid.uuid4())
    safe_name = file.filename.replace("/", "_").replace("\\", "_")
    dest = DOCS / f"{doc_id}_{safe_name}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    text = extract(dest)
    if not text.strip():
        return {"status": "error", "message": "Empty or unsupported file"}
    chunks = chunk(text)
    embeddings = embedder.encode(chunks).tolist()
    ids = [f"{doc_id}__{i}" for i in range(len(chunks))]
    metas = [{"doc_id": doc_id, "filename": file.filename, "title": title or file.filename, "chunk_idx": i, "size": dest.stat().st_size, "ext": dest.suffix.lower()} for i in range(len(chunks))]
    collection.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metas)
    return {"status": "ok", "doc_id": doc_id, "filename": file.filename, "chunks": len(chunks), "total": collection.count()}

@app.delete("/documents/{doc_id}")
async def delete_doc(doc_id: str, request: Request):
    require_user(request)
    existing = collection.get(where={"doc_id": doc_id}, include=["ids"])
    if existing and existing.get("ids"):
        collection.delete(ids=existing["ids"])
    return {"status": "ok", "deleted": len(existing.get("ids", []))}

@app.delete("/documents")
async def clear_all(request: Request):
    require_user(request)
    ids = collection.get(include=["metadatas"]).get("ids", []) or []
    if ids:
        collection.delete(ids=ids)
    return {"status": "ok", "deleted": len(ids)}

@app.post("/query")
async def query(body: Query, request: Request):
    require_user(request)
    q_emb = embedder.encode([body.question]).tolist()[0]
    res = collection.query(query_embeddings=[q_emb], n_results=body.top_k, include=["documents", "metadatas", "distances"])
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0] if "distances" in res else []
    ctx = "\n\n---\n\n".join(docs)
    prompt = f"You are VIDE IT ai. Use the following document excerpts to answer. If not found, say so.\n\nCONTEXT:\n{ctx}\n\nQUESTION: {body.question}\nANSWER:"
    async with httpx.AsyncClient(timeout=180) as cli:
        resp = await cli.post(f"{OLLAMA_URL}/api/generate", json={"model": MODEL, "prompt": prompt, "stream": False, "options": {"num_ctx": 65536}})
        out = resp.json()
    return {"answer": out.get("response", ""), "sources": [{"filename": m.get("filename"), "title": m.get("title"), "distance": round(float(d or 0), 4)} for m, d in zip(metas, dists)], "used_model": MODEL}

@app.get("/documents")
async def list_docs(request: Request):
    require_user(request)
    metas = collection.get(include=["metadatas"]).get("metadatas", []) or []
    seen = {}
    for m in metas:
        did = m.get("doc_id")
        if did and did not in seen:
            seen[did] = {"doc_id": did, "filename": m.get("filename"), "title": m.get("title"), "size": m.get("size"), "ext": m.get("ext")}
    return {"total": len(seen), "documents": list(seen.values())}

# =================== HELPERS ===================

def extract(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    if suffix in (".docx", ".doc"):
        try:
            from docx import Document
            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception:
            pass
    if suffix in (".txt", ".md", ".py", ".js", ".json", ".yaml", ".yml", ".log", ".cpp", ".c", ".h", ".java", ".go", ".rs", ".ts", ".tsx", ".jsx", ".sql", ".sh", ".bash"):
        return path.read_text(errors="ignore")
    try:
        return path.read_text(errors="ignore")
    except Exception:
        return ""

def chunk(text: str, max_words: int = 500, overlap: int = 100) -> list:
    words = text.split()
    if not words:
        return []
    out, start = [], 0
    while start < len(words):
        end = min(start + max_words, len(words))
        out.append(" ".join(words[start:end]))
        start = end - overlap
        if start >= len(words):
            break
    return out
