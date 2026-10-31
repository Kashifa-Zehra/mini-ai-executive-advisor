# Mini AI Executive Advisory System

Streamlit app: document Q&A with citations, Finance + HR advisors with auto-routing, role-based access control, upload automation, audit log.

## Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python seed.py                      # loads synthetic sample docs (incl. conflicts + a confidential file)
export ANTHROPIC_API_KEY=sk-...     # optional; without it the app uses extractive fallback answers
streamlit run app.py
pytest -q                           # Task 6 reliability tests
```
Logins (demo only): `admin/admin123` (Administrator), `alice/user123` (Standard User).

**Note:** the LLM layer is optional. Without `ANTHROPIC_API_KEY`, the app runs in fallback mode: retrieval, access control, routing, conflict detection and financial calculations still work, and answers show the best matching passage. Setting the key enables written answers with citations.

## Architecture
```mermaid
flowchart TD
    U[User: Admin or Standard] --> UI[Streamlit UI + login]
    UI --> R{Router}
    R --> F[Finance Advisor]
    R --> H[HR Advisor]
    R --> K[Knowledge Advisor]
    F & H & K --> S[Search: ACL filter in SQL, then BM25]
    S --> DB[(SQLite: chunks, audit, reports, outbox)]
    S -->|permitted chunks only| L[LLM, optional]
    F --> C[Python calculations]
    UI --> UP[Upload automation]
    UP --> DB
    UP --> N[Email / outbox notification]
```

**Security:** classification filtering happens in the database query *before* retrieval, so restricted text never enters the prompt. Standard users get the same "insufficient evidence" reply whether a document is absent or restricted (no existence leak). Only admins may upload confidential files. All asks, denials, failed logins and failures are audited.

## Reliability (tests/test_core.py)
Missing info, conflicting HR docs, conflicting finance figures, unauthorized retrieval (search, ask, upload), LLM outage fallback, finance math and `verify_figure` for incorrect figures, routing, automation.

## Deployment / operations
Docker container behind HTTPS reverse proxy (nginx/Caddy) with SSO (OIDC) replacing demo logins; secrets in env/secret manager; move SQLite to Postgres and BM25 to a vector DB (pgvector/Chroma) for scale; encrypt storage; ship audit table to central logging with alerts on `DENIED_*`/`llm_failure`; health check, backups, dependency scanning, rate limiting; expand with more advisors, OCR, Slack/Teams webhooks.

## Declaration
- Components I personally developed: set up and ran the system locally, tested every scenario in the brief, found and fixed a UI bug (success message shown before the upload was processed), applied an overwrite-protection security fix, resolved formatting and syntax issues, and prepared the deployment notes.
- AI tools used: Claude (generated the initial scaffold, which I reviewed, tested and modified)
- External assistance: none beyond the AI tool
