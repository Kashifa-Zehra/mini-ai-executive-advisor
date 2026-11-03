import os, sys, tempfile, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["ADVISOR_DB"] = tempfile.mktemp(suffix=".db"); os.environ.pop("ANTHROPIC_API_KEY", None)
import core, seed   # seed loads synthetic docs

def test_missing_information():
    assert core.NO_EVIDENCE in core.ask("What is the office parking policy?", "alice", "user")["answer"]

def test_conflicting_hr_documents():
    r = core.ask("How many days of annual leave do employees get?", "alice", "user")
    assert "CONFLICT" in r["answer"] and set(r["sources"]) >= {"hr_policy_2024.txt", "hr_handbook_2026.txt"}

def test_unauthorized_access_blocked_everywhere():
    assert core.search("CEO salary", "user") == []
    r = core.ask("What is the CEO salary?", "alice", "user")
    assert "400,000" not in r["answer"] and "salary_bands_confidential.txt" not in r["sources"]
    assert "400,000" in core.ask("What is the CEO salary?", "admin", "admin")["answer"]
    with pytest.raises(PermissionError): core.process_upload("x.txt", b"hi", "confidential", "alice", "user")

def test_llm_failure_falls_back():
    os.environ["ANTHROPIC_API_KEY"] = "bad"
    os.environ["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:9"  # unreachable -> simulated API outage
    r = core.ask("annual leave days", "alice", "user"); os.environ.pop("ANTHROPIC_API_KEY")
    assert "LLM unavailable" in r["answer"]

def test_finance_calculations():
    assert round(core.growth(1200000, 1500000),6) == 25.0 and round(core.margin(210000, 1500000),6) == 14.0
    with pytest.raises(ValueError): core.growth(0, 5)
    assert core.verify_figure(25.0, core.growth(1200000, 1500000)) and not core.verify_figure(30.0, 25.0)
    r = core.ask("What is revenue growth and net margin?", "alice", "user")
    assert "25.0%" in r["answer"] and "14.0%" in r["answer"] and "CONFLICT" in r["answer"]  # budget memo conflicts

def test_routing():
    assert core.route("net profit margin") == "finance" and core.route("sick leave policy") == "hr"

def test_automation(tmp_path):
    core.process_upload("q3.txt", b"Q3 was strong. Sales grew. More text.", "public", "admin", "admin")
    with core.conn() as c: assert c.execute("select count(*) from reports where doc='q3.txt'").fetchone()[0] == 1

def test_standard_user_cannot_overwrite_restricted_doc():
    with pytest.raises(PermissionError): core.process_upload("salary_bands_confidential.txt", b"overwritten", "public", "alice", "user")
    assert "400,000" in core.ask("What is the CEO salary?", "admin", "admin")["answer"]   # original content intact
    with core.conn() as c: assert c.execute("select count(*) from audit where event='DENIED_overwrite'").fetchone()[0] >= 1

# ---- regression: period ordering (Q4 2024 -> Q1 2025 was reported as -50% instead of +100%) ----
def test_growth_quarters_chronological():
    assert core.growth_lines({"Q42024": 100, "Q12025": 200}) == ["Revenue growth Q42024->Q12025: 100.0%  [computed in Python]"]

def test_growth_decrease_unchanged_and_order_independent():
    assert "-50.0%" in core.growth_lines({"Q42024": 200, "Q12025": 100})[0]
    assert "0.0%" in core.growth_lines({"Q12025": 150, "Q42024": 150})[0]
    assert core.growth_lines({"Q12025": 200, "Q42024": 100}) == core.growth_lines({"Q42024": 100, "Q12025": 200})  # input order irrelevant

def test_growth_zero_or_missing_baseline():
    assert "not computed" in core.growth_lines({"Q42024": 0, "Q12025": 200})[0]
    assert core.growth_lines({"Q12025": 200}) == []                      # single period: nothing to compare

def test_growth_never_mixes_quarters_and_years():
    out = core.growth_lines({"2024": 1000, "2025": 1500, "Q42024": 100, "Q12025": 200})
    assert any("2024->2025: 50.0%" in x for x in out) and any("Q42024->Q12025: 100.0%" in x for x in out)
    assert core.growth_lines({"2025": 1500, "Q42024": 100}) == []         # year vs quarter must not be compared

def test_end_to_end_quarter_order_from_documents():
    core.ingest("zz_quarterly.txt", "Quarterly\n\nRevenue Q1 2025: 200\nRevenue Q4 2024: 100\n", "public")   # stored newest-first on purpose
    try: ans = core.ask("What is revenue growth?", "alice", "user")["answer"]
    finally:
        with core.conn() as c: c.execute("delete from chunks where doc='zz_quarterly.txt'")
    assert "Q42024->Q12025: 100.0%" in ans and "-50" not in ans

# ---- stricter evidence + topic-specific conflicts + conflict transparency ----
def test_partial_keyword_match_is_not_evidence():
    for q in ("What is the notice period?", "What is the maternity leave policy?"):
        assert core.NO_EVIDENCE in core.ask(q, "alice", "user")["answer"], q

def test_known_questions_still_answered():
    assert "3 months" in core.ask("What is the probation period?", "alice", "user")["answer"]
    assert "10 days" in core.ask("How many sick leave days do employees get?", "alice", "user")["answer"]

def test_conflict_shown_only_for_topic_asked():
    sick = core.ask("How many sick leave days do employees get?", "alice", "user")["answer"]
    assert "CONFLICT" not in sick                                       # annual-leave conflict is irrelevant here
    assert "CONFLICT" in core.ask("What is the annual leave policy?", "alice", "user")["answer"]
    assert "CONFLICT" in core.ask("What is the leave policy?", "alice", "user")["answer"]   # generic question: still warn

def test_finance_conflict_states_value_used():
    ans = core.ask("What is revenue growth and net margin?", "alice", "user")["answer"]
    assert "calculations above use" in ans and "1,480,000" in ans and "1,500,000" in ans

def test_confidential_doc_cannot_be_downgraded_even_by_admin():
    with pytest.raises(PermissionError): core.process_upload("salary_bands_confidential.txt", b"now public", "public", "admin", "admin")
    assert core.search("CEO salary", "user") == []                          # still hidden from standard users
    with pytest.raises(PermissionError): core.process_upload("salary_bands_confidential.txt", b"x", "public", "alice", "user")
    with core.conn() as c: assert c.execute("select count(*) from audit where event='DENIED_downgrade'").fetchone()[0] >= 1
