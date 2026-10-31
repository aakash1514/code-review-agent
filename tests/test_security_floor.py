"""The security agent's deterministic CRITICAL floor for canonical vulnerabilities."""
import pytest

from agents.security_agent import _ceil_context_dependent_classes, _floor_canonical_vulnerabilities


def _added(*lines):
    """A new-file patch whose added lines are `lines`."""
    return f"@@ -0,0 +1,{len(lines)} @@\n" + "\n".join("+" + l for l in lines)


def _sec(make_finding, title, snippet="", severity="HIGH", description=None):
    return make_finding(title=title, description=description, severity=severity, category="SECURITY",
                        snippet=snippet)


def _floored(finding, diff=""):
    [f] = _floor_canonical_vulnerabilities([finding], diff)
    return f.severity.value


SQLI_FSTRING = ['    rows = conn.execute(',
                '        f"SELECT id FROM members WHERE name LIKE \'{name}%\'"',
                '    ).fetchall()']


# --- positives: wording + code evidence --------------------------------------------

@pytest.mark.parametrize("title, snippet, diff", [
    ("SQL injection via f-string query", "", _added(*SQLI_FSTRING)),
    ("SQL injection in search", 'query = "SELECT * FROM t WHERE id = %s" % user_id\ncur.execute(query)', ""),
    ("SQL injection through .format", 'db.execute("DELETE FROM t WHERE id = {}".format(i))', ""),
    ("OS command injection in backup", "subprocess.run(cmd, shell=True, check=True)", ""),
    ("Shell injection", "", _added('    os.system("tar czf /backups/" + folder)')),
    ("Command injection", 'subprocess.check_output(f"ping {host}")', ""),
    ("Hardcoded API key", 'PAYLANE_API_KEY = "plk_live_4f9c2a7e81d34b6c9a0e5f17d2b8c3a6"', ""),
    ("Hard-coded secret token", "", _added('GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz"')),
    ("Insecure deserialization of cookie", "return pickle.loads(base64.urlsafe_b64decode(raw))", ""),
    ("Unsafe yaml.load on upload", "cfg = yaml.load(body)", ""),
    ("Missing authorization check on delete route", "def delete_user(user_id):",
     _added('@bp.route("/users/<int:user_id>", methods=["DELETE"])', "def delete_user(user_id):")),
    ("Endpoint without auth", '@app.post("/tenants")', ""),
    ("Route lacks authentication", '@bp.route("/items/<int:i>", methods=["PUT", "PATCH"])\ndef edit(i):', ""),
])
def test_canonical_class_with_code_evidence_is_raised(make_finding, capsys, title, snippet, diff):
    assert _floored(_sec(make_finding, title, snippet), diff) == "CRITICAL"
    assert "[security_agent] raised HIGH->CRITICAL (" in capsys.readouterr().out


def test_medium_is_raised_too(make_finding):
    assert _floored(_sec(make_finding, "SQL injection", "", severity="MEDIUM"), _added(*SQLI_FSTRING)) == "CRITICAL"


# --- negatives: no code evidence for the class --------------------------------------

@pytest.mark.parametrize("title, snippet", [
    ("SQL injection risk", 'conn.execute("SELECT id FROM members WHERE id = ?", (member_id,))'),
    ("SQL injection risk", 'cur.execute("UPDATE t SET name = %s WHERE id = %s", (name, i))'),
    ("Command injection risk", 'subprocess.run(["tar", "czf", dest, folder], check=True)'),
    ("Hardcoded API key", 'API_KEY = os.environ["API_KEY"]'),
    ("Hardcoded password", 'PASSWORD = os.getenv("DB_PASSWORD", "x9f8a7b6c5d4e3f2a1")'),
    ("Hardcoded secret", 'SECRET_KEY = "changeme"'),
    ("Hardcoded secret", 'SECRET_KEY = "changeme-please-1234567"'),
    ("Hardcoded token", 'TOKEN = "your_token_goes_here_1234"'),
    ("Hardcoded API key", 'API_KEY = ""'),
    ("Unsafe yaml deserialization", "cfg = yaml.safe_load(body)"),
    ("Unsafe yaml deserialization", "cfg = yaml.load(body, Loader=yaml.SafeLoader)"),
    ("Missing authentication check", '@bp.route("/users")'),
    ("Missing authentication check", '@bp.route("/users", methods=["GET"])'),
    ("Missing auth on export", '@app.get("/export")'),
])
def test_no_code_evidence_is_not_raised(make_finding, title, snippet):
    assert _floored(_sec(make_finding, title, snippet)) == "HIGH"


def test_wording_without_code_evidence_anywhere_is_not_raised(make_finding):
    diff = _added("def total(items):", "    return sum(items)")
    for title in ("SQL injection", "Command injection", "Hardcoded API key", "Insecure deserialization",
                  "Missing authorization check"):
        assert _floored(_sec(make_finding, title, "return sum(items)"), diff) == "HIGH"


# --- missing auth: evidence must be local to the finding ----------------------------

# sec-08's shape: an added DELETE route whose body the finding quotes.
SEC_08_PATCH = """\
@@ -24,3 +24,13 @@
     user.active = False
     db.session.commit()
     return jsonify(user.to_dict())
+
+
+@bp.route("/users/<int:user_id>", methods=["DELETE"])
+@login_required
+def delete_user(user_id: int):
+    \"\"\"Permanently delete a user and all of their data.\"\"\"
+    user = User.query.get_or_404(user_id)
+    db.session.delete(user)
+    db.session.commit()
+    return "", 204"""

MISSING_AUTH = "Missing authorization check on user deletion"


def test_missing_auth_raised_when_the_route_decorator_is_a_few_lines_above_the_snippet(make_finding):
    finding = _sec(make_finding, MISSING_AUTH, "user = User.query.get_or_404(user_id)")
    assert _floored(finding, SEC_08_PATCH) == "CRITICAL"


def test_missing_auth_not_raised_when_the_decorator_is_more_than_5_added_lines_above(make_finding):
    # db.session.delete(user) is the 6th added line counting up from the decorator
    assert _floored(_sec(make_finding, MISSING_AUTH, "db.session.delete(user)"), SEC_08_PATCH) == "HIGH"


def test_missing_auth_not_raised_for_a_snippet_in_another_function(make_finding):
    diff = _added('@bp.route("/items", methods=["POST"])', "def create_item():", "    return save(request.json)",
                  "", "", "def purge():", "    a = load()", "    b = filter(a)", "    delete_all(b)")
    assert _floored(_sec(make_finding, "Missing authentication check on purge", "delete_all(b)"), diff) == "HIGH"


def test_missing_auth_not_raised_without_a_local_snippet(make_finding):
    diff = _added('@bp.route("/items", methods=["POST"])', "def create_item():")
    assert _floored(_sec(make_finding, MISSING_AUTH, ""), diff) == "HIGH"                # empty snippet
    assert _floored(_sec(make_finding, MISSING_AUTH, "not in the diff"), diff) == "HIGH"  # not found
    # a snippet only in context lines isn't in the added lines
    assert _floored(_sec(make_finding, MISSING_AUTH, "return jsonify(user.to_dict())"), SEC_08_PATCH) == "HIGH"


def test_unauthenticated_attacker_wording_is_not_missing_auth(make_finding):
    # SSRF-style finding that never says "SSRF", in a diff with a POST route
    diff = _added('@bp.route("/previews", methods=["POST"])', "def create_preview():",
                  '    url = request.json["url"]', "    resp = requests.get(url, timeout=5)")
    finding = _sec(make_finding, "Server fetches arbitrary user-supplied URLs", "resp = requests.get(url, timeout=5)",
                   description="An unauthenticated attacker can fetch internal URLs through the preview endpoint.")
    assert _floored(finding, diff) == "HIGH"


def test_code_evidence_without_the_class_wording_is_not_raised(make_finding):
    assert _floored(_sec(make_finding, "Unvalidated input", "subprocess.run(cmd, shell=True)")) == "HIGH"


# --- never raised ------------------------------------------------------------------

@pytest.mark.parametrize("severity", ["LOW", "INFO"])
def test_low_and_info_are_not_raised(make_finding, severity):
    finding = _sec(make_finding, "SQL injection", "", severity=severity)
    assert _floored(finding, _added(*SQLI_FSTRING)) == severity


@pytest.mark.parametrize("title, description, diff", [
    ("Path traversal in report download", "missing authorization check; send_file(os.path.join(base, filename))",
     _added('@bp.route("/reports/<path:filename>", methods=["POST"])')),
    ("Weak password hashing with MD5", "hardcoded salt and api key handling; hashlib.md5",
     _added('API_KEY = "sk_live_abcdefghijklmnop1234"')),
    ("SSRF: server fetches a user-supplied URL", "unauthenticated POST endpoint fetches any URL",
     _added('@bp.route("/previews", methods=["POST"])', "    resp = requests.get(url)")),
])
def test_excluded_classes_are_never_raised(make_finding, title, description, diff):
    assert _floored(_sec(make_finding, title, "", description=description), diff) == "HIGH"


def test_severity_is_never_lowered_and_input_is_not_mutated(make_finding):
    critical = _sec(make_finding, "Something else entirely", "x = 1", severity="CRITICAL")
    high = _sec(make_finding, "Command injection", "os.system(cmd)")
    out = _floor_canonical_vulnerabilities([critical, high], "")
    assert [f.severity.value for f in out] == ["CRITICAL", "CRITICAL"]
    assert high.severity.value == "HIGH"


# --- ceiling: context-dependent classes are capped at HIGH --------------------------

def _ceiled(finding, diff=""):
    [f] = _ceil_context_dependent_classes([finding], diff)
    return f.severity.value


@pytest.mark.parametrize("title, snippet, keyword", [
    ("Server-Side Request Forgery (SSRF) via unvalidated URL",
     'url = request.json["url"]\nresp = requests.get(url, timeout=5, allow_redirects=True)', "ssrf"),
    ("Path traversal in report download", "return send_file(os.path.join(REPORTS_DIR, filename))", "path traversal"),
    ("Passwords hashed with MD5", "hashlib.md5(password.encode()).hexdigest()", "md5"),
])
def test_context_dependent_critical_is_capped_to_high(make_finding, capsys, title, snippet, keyword):
    assert _ceiled(_sec(make_finding, title, snippet, severity="CRITICAL")) == "HIGH"
    assert f"[security_agent] capped CRITICAL->HIGH (context-dependent: {keyword}): " in capsys.readouterr().out


def test_never_raise_keyword_with_a_canonical_class_stays_critical(make_finding, capsys):
    finding = _sec(make_finding, "Path traversal enables pickle.loads of attacker file",
                   "obj = pickle.loads(open(os.path.join(UPLOADS, name), 'rb').read())", severity="CRITICAL")
    assert _ceiled(finding) == "CRITICAL"
    assert "capped" not in capsys.readouterr().out


def test_canonical_critical_without_never_raise_words_is_untouched(make_finding):
    assert _ceiled(_sec(make_finding, "SQL injection", "", severity="CRITICAL"), _added(*SQLI_FSTRING)) == "CRITICAL"
    assert _ceiled(_sec(make_finding, "Something else entirely", "x = 1", severity="CRITICAL")) == "CRITICAL"


@pytest.mark.parametrize("severity", ["HIGH", "MEDIUM", "LOW", "INFO"])
def test_ceiling_touches_only_critical(make_finding, severity):
    assert _ceiled(_sec(make_finding, "SSRF via user URL", "requests.get(url)", severity=severity)) == severity


def test_ceiling_does_not_mutate_input(make_finding):
    finding = _sec(make_finding, "SSRF via user URL", "requests.get(url)", severity="CRITICAL")
    _ceil_context_dependent_classes([finding], "")
    assert finding.severity.value == "CRITICAL"
