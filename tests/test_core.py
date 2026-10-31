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

def test_cannot_overwrite_restricted_document():
    with pytest.raises(PermissionError):
        core.process_upload("salary_bands_confidential.txt", b"fake", "public", "alice", "user")
    assert "400,000" in core.ask("What is the CEO salary?", "admin", "admin")["answer"]
