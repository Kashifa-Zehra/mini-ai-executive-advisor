import streamlit as st, core
st.set_page_config(page_title="Mini AI Executive Advisor", layout="wide")
if "role" not in st.session_state: st.session_state.role = None
with st.sidebar:
    st.title("Login")
    if not st.session_state.role:
        u, p = st.text_input("User"), st.text_input("Password", type="password")
        if st.button("Sign in"):
            r = core.login(u, p)
            if r: st.session_state.update(role=r, user=u); st.rerun()
            else: core.log(u or "?", "FAILED_login"); st.error("Invalid credentials")
        st.caption("Demo: admin/admin123 (Administrator), alice/user123 (Standard User)")
        st.stop()
    st.success(f"{st.session_state.user} ({st.session_state.role})")
    if st.button("Sign out"): st.session_state.clear(); st.rerun()
user, role = st.session_state.user, st.session_state.role
t1, t2, t3 = st.tabs(["Ask the Advisors", "Upload & Automate", "Admin: Audit & Reports"])
with t1:
    q = st.text_input("Ask a question (auto-routed to Finance / HR / Knowledge advisor)")
    if q:
        r = core.ask(q, user, role)
        st.caption(f"Routed to: **{r['advisor']}**"); st.markdown(r["answer"].replace("$", "\\$"))
        st.info("Sources: " + (", ".join(r["sources"]) or "none"))
with t2:
    f = st.file_uploader("Upload PDF / Word / text", type=["pdf", "docx", "txt"])
    cls = st.selectbox("Classification", ["public", "confidential"])
    if f and st.button("Process"):
        try:
            summary = core.process_upload(f.name, f.read(), cls, user, role)
            st.success("Processed, stored, notification queued.")
            st.write(summary)
        except PermissionError as e: st.error(str(e))
        except Exception as e: core.log(user, "upload_failure", e); st.error("Could not process that file.")
with t3:
    if role != "admin": st.warning("Administrators only.")
    else:
        with core.conn() as c:
            for tbl in ("reports", "outbox", "audit"):
                st.subheader(tbl); st.dataframe([dict(r) for r in c.execute(f"select * from {tbl} order by ts desc limit 50")])
