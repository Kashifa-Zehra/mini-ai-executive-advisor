"""Mini AI Executive Advisory System - core logic (no UI)."""
import os, re, math, sqlite3, hashlib, collections, datetime, smtplib
from email.message import EmailMessage

DB = os.environ.get("ADVISOR_DB", "advisor.db")
MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-5-5")
ALLOWED = {"admin": ("public", "confidential"), "user": ("public",)}   # role -> classifications
h = lambda p: hashlib.sha256(p.encode()).hexdigest()
USERS = {"admin": ("admin", h("admin123")), "alice": ("user", h("user123"))}  # DEMO ONLY
NO_EVIDENCE = "I cannot find sufficient evidence in the documents available to you."
STOP = set("the a an of to in is are what for and or on with how do does our my i it be by from at as many much get give tell about me we you your can will would should please there any which who when where why have has had was were this that if policy policies".split())

def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.executescript("""create table if not exists chunks(doc text, cls text, idx int, text text);
    create table if not exists audit(ts text, user text, event text, detail text);
    create table if not exists reports(ts text, doc text, summary text);
    create table if not exists outbox(ts text, to_addr text, subject text, body text);""")
    return c

def log(user, event, detail=""):
    with conn() as c: c.execute("insert into audit values(?,?,?,?)", (datetime.datetime.now().isoformat(timespec="seconds"), user, event, str(detail)[:300]))

def login(u, p):
    r = USERS.get(u)
    return r[0] if r and r[1] == h(p) else None

# ---------- ingestion ----------
def read_file(name, data: bytes) -> str:
    ext = name.lower().rsplit(".", 1)[-1]
    if ext == "pdf":
        import io; from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    if ext == "docx":
        import io; from docx import Document
        return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    return data.decode("utf-8", errors="ignore")

def chunk(text, size=700):
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if len(cur) + len(para) > size and cur: out.append(cur.strip()); cur = ""
        cur += para + "\n\n"
    return out + [cur.strip()] if cur.strip() else out

def ingest(name, text, cls="public"):
    with conn() as c:
        c.execute("delete from chunks where doc=?", (name,))
        c.executemany("insert into chunks values(?,?,?,?)", [(name, cls, i, t) for i, t in enumerate(chunk(text))])

# ---------- retrieval (ACL applied in SQL BEFORE scoring, so restricted text never reaches the LLM) ----------
tok = lambda t: [w.rstrip("s") for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in STOP]

def search(query, role, k=4, min_cov=0.6):
    cls = ALLOWED.get(role, ())
    if not cls: return []
    with conn() as c:
        rows = c.execute(f"select * from chunks where cls in ({','.join('?'*len(cls))})", cls).fetchall()
    if not rows: return []
    docs = [tok(r["text"]) for r in rows]; q = set(tok(query)); N = len(docs)
    avg = sum(map(len, docs)) / N or 1
    df = collections.Counter(w for d in docs for w in set(d))
    hits = []
    # evidence threshold: short questions (<=3 key terms) must match ALL terms, longer ones at least min_cov of them
    need = len(q) if (len(q) <= 3 and min_cov >= 0.5) else max(1, math.ceil(min_cov * len(q) - 1e-9))
    for r, d in zip(rows, docs):
        tf = collections.Counter(d); m = q & set(d)
        if not q or len(m) < need: continue
        s = sum(math.log(1 + (N - df[w] + .5) / (df[w] + .5)) * tf[w] * 2.5 / (tf[w] + 1.5 * (.25 + .75 * len(d) / avg)) for w in m)
        hits.append({"doc": r["doc"], "idx": r["idx"], "text": r["text"], "score": round(s, 2)})
    return sorted(hits, key=lambda x: -x["score"])[:k]

# ---------- LLM with graceful failure ----------
def llm(system, prompt):
    """Returns text, or None on any failure (no key, network, API error) -> caller falls back."""
    if not os.environ.get("ANTHROPIC_API_KEY"): return None
    for attempt in range(2):
        try:
            import anthropic
            r = anthropic.Anthropic(timeout=30).messages.create(model=MODEL, max_tokens=700, system=system,
                                                               messages=[{"role": "user", "content": prompt}])
            return r.content[0].text
        except Exception as e:
            log("system", "llm_failure", f"attempt {attempt+1}: {e}")
    return None

def ctx(hits): return "\n\n".join(f"[{i+1}] (source: {x['doc']})\n{x['text']}" for i, x in enumerate(hits))
def sources(hits): return sorted({x["doc"] for x in hits})
GROUND = "Answer ONLY from the numbered sources. Cite as [n]. If they do not contain the answer, reply exactly: " + NO_EVIDENCE

def knowledge_answer(q, role):
    hits = search(q, role)
    if not hits: return {"answer": NO_EVIDENCE, "sources": [], "advisor": "knowledge"}
    a = llm(GROUND, f"Sources:\n{ctx(hits)}\n\nQuestion: {q}")
    if a is None: a = "(LLM unavailable - showing best matching passage)\n\n" + hits[0]["text"]
    return {"answer": a, "sources": sources(hits), "advisor": "knowledge"}

# ---------- finance tools: plain Python, never LLM arithmetic ----------
def growth(old, new):
    if old == 0: raise ValueError("Cannot compute growth from a zero base")
    return (new - old) / abs(old) * 100
def margin(profit, revenue):
    if revenue <= 0: raise ValueError("Revenue must be positive")
    return profit / revenue * 100
def verify_figure(claimed, computed, tol=0.01):
    """True if an (LLM- or human-stated) figure matches the code-computed one."""
    return abs(claimed - computed) <= tol * max(abs(computed), 1e-9)

def period_key(p):
    """Chronological sort key. Returns (kind, year, quarter): 'Q' for quarters, 'Y' for years. None if unrecognised."""
    m = re.fullmatch(r"Q([1-4])(\d{4})", p)
    if m: return ("Q", int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"(?:FY)?(\d{4})", p)
    return ("Y", int(m.group(1)), 0) if m else None

def growth_lines(rev):
    """rev: {period_label: revenue}. Compares the two latest periods of the SAME kind (quarter vs quarter, year vs year),
    in chronological order - never by text order. Returns display lines."""
    out = []
    for kind in ("Y", "Q"):
        series = sorted((period_key(p), p, v) for p, v in rev.items() if period_key(p) and period_key(p)[0] == kind)
        if len(series) < 2: continue
        (_, a, old), (_, b, new) = series[-2], series[-1]
        try: out.append(f"Revenue growth {a}->{b}: {growth(old, new):.1f}%  [computed in Python]")
        except ValueError as e: out.append(f"Revenue growth {a}->{b}: not computed ({e})")
    return out

FIN_RE = re.compile(r"(?i)(revenue|net income|expenses?)\s*(?:for\s*)?(FY\s?\d{4}|Q[1-4]\s?\d{4}|\d{4})\s*[:=]\s*\$?\s*([\d,]+(?:\.\d+)?)")
def extract_metrics(hits):
    out = collections.defaultdict(list)
    for x in hits:
        for m, per, v in FIN_RE.findall(x["text"]):
            out[(m.lower().rstrip("s"), per.upper().replace(" ", ""))].append((float(v.replace(",", "")), x["doc"]))
    return out

def conflicts(facts):
    """facts: {key: [(value, doc)]} -> keys where different documents give different values."""
    return {k: v for k, v in facts.items() if len({val for val, _ in v}) > 1 and len({d for _, d in v}) > 1}

def finance_answer(q, role):
    hits = search(q + " revenue net income expenses", role, k=8, min_cov=0.1)
    met = extract_metrics(hits)
    if not met: return {"answer": NO_EVIDENCE + " (no revenue/income/expense figures found)", "sources": [], "advisor": "finance"}
    lines, conf = [], conflicts(met)
    val = {k: v[0][0] for k, v in met.items()}
    for k, v in sorted(met.items()): lines.append(f"- {k[0].title()} {k[1]}: {v[0][0]:,.0f}  (source: {v[0][1]})")
    periods = sorted({p for _, p in met})
    calc = []
    try:
        calc += growth_lines({p: val[("revenue", p)] for p in periods if ("revenue", p) in val})
        for p in periods:
            if ("net income", p) in val and ("revenue", p) in val: calc.append(f"Net margin {p}: {margin(val[('net income', p)], val[('revenue', p)]):.1f}%  [computed in Python]")
    except ValueError as e: calc.append(f"Calculation error handled: {e}")
    body = "**Extracted figures**\n" + "\n".join(lines) + "\n\n**Calculations**\n" + "\n".join(f"- {c}" for c in calc or ["not enough data"])
    if conf: body += "\n\n**CONFLICT**\n" + "\n".join(f"- {k[0].title()} {k[1]}: " + " vs ".join(f"{v:,.0f} ({d})" for v, d in vs) + f"  -> calculations above use {vs[0][0]:,.0f} ({vs[0][1]}); confirm the authoritative figure" for k, vs in conf.items())
    an = llm("You are a finance advisor. Write a 3-sentence analysis using ONLY these verified figures. Do not do any new arithmetic.", body)
    return {"answer": body + "\n\n**Analysis**\n" + (an or "(LLM unavailable - figures above are code-verified.)"), "sources": sources(hits), "advisor": "finance"}

# ---------- HR advisor ----------
HR_RE = re.compile(r"(?i)(annual leave|sick leave|probation|notice period|remote work)[^.\n]{0,60}?(\d+)\s*(?:working |calendar )?(days?|weeks?|months?)")
def hr_answer(q, role):
    hits = search(q, role)
    if not hits: return {"answer": NO_EVIDENCE + " This may be missing from the HR policy set - please confirm with HR.", "sources": [], "advisor": "hr"}
    facts = collections.defaultdict(list)
    for x in search(q, role, k=10):
        for t, n, u in HR_RE.findall(x["text"]): facts[t.lower()].append((f"{n} {u.rstrip('s')}s", x["doc"]))
    conf = conflicts(facts)
    qt = set(tok(q))
    asked = {k for k in facts if set(tok(k)) <= qt}                       # HR topics the question explicitly names
    if asked: conf = {k: v for k, v in conf.items() if k in asked}        # only report conflicts on those topics
                                                                          # (generic question, e.g. "leave policy": show all)
    a = llm(GROUND + " Also state if sources conflict or information is missing.", f"Sources:\n{ctx(hits)}\n\nQuestion: {q}") or "(LLM unavailable)\n\n" + hits[0]["text"]
    if conf: a += "\n\n**CONFLICT DETECTED - verify with HR before acting:**\n" + "\n".join(f"- {k}: " + " vs ".join(f"{v} ({d})" for v, d in vs) for k, vs in conf.items())
    return {"answer": a, "sources": sources(hits), "advisor": "hr"}

# ---------- router ----------
FIN_KW = set("revenue profit margin cost expense budget growth ratio cash roi income sales quarter financial finance ebitda".split())
HR_KW = set("leave vacation holiday sick policy employee remote hiring probation notice benefit overtime manager salary hr".split())
def route(q):
    t = set(re.findall(r"[a-z]+", q.lower())); f, hr = len(t & FIN_KW), len(t & HR_KW)
    return "finance" if f > hr else "hr" if hr > f else "knowledge"

def ask(q, user, role):
    adv = route(q)
    try: r = {"finance": finance_answer, "hr": hr_answer, "knowledge": knowledge_answer}[adv](q, role)
    except Exception as e:
        log(user, "processing_error", e); r = {"answer": "Sorry, processing failed safely. Please try again.", "sources": [], "advisor": adv}
    log(user, "ask", f"{adv}|{role}|{q}|sources={r['sources']}")
    return r

# ---------- automation: upload -> summarize -> DB record -> email notification ----------
def notify(to, subject, body):
    with conn() as c: c.execute("insert into outbox values(?,?,?,?)", (datetime.datetime.now().isoformat(timespec="seconds"), to, subject, body))
    if os.environ.get("SMTP_HOST"):
        try:
            m = EmailMessage(); m["To"], m["From"], m["Subject"] = to, os.environ.get("SMTP_FROM", "advisor@example.com"), subject; m.set_content(body)
            with smtplib.SMTP(os.environ["SMTP_HOST"], 587, timeout=10) as s:
                s.starttls(); s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"]); s.send_message(m)
        except Exception as e: log("system", "email_failure", e)

def process_upload(name, data, cls, user, role, notify_to="manager@example.com"):
    if cls == "confidential" and role != "admin":
        log(user, "DENIED_upload_confidential", name); raise PermissionError("Only administrators may upload confidential documents")
    with conn() as c: ex = c.execute("select cls from chunks where doc=? limit 1", (name,)).fetchone()
    if ex and ex["cls"] not in ALLOWED.get(role, ()):
        log(user, "DENIED_overwrite", name); raise PermissionError("A restricted document with this name already exists")
    if ex and ex["cls"] == "confidential" and cls != "confidential":      # nobody (even admin) may silently downgrade a confidential doc
        log(user, "DENIED_downgrade", name); raise PermissionError("A confidential document cannot be re-uploaded as public")
    text = read_file(name, data); ingest(name, text, cls)
    s = llm("Summarise this business document in 3 bullet points.", text[:6000]) or " ".join(re.split(r"(?<=[.!?])\s", text.strip())[:2])
    with conn() as c: c.execute("insert into reports values(?,?,?)", (datetime.datetime.now().isoformat(timespec="seconds"), name, s))
    notify(notify_to, f"New {cls} document processed: {name}", s if cls == "public" else "A confidential document was processed. Summary stored in the admin dashboard.")
    log(user, "upload", f"{name}|{cls}"); return s
