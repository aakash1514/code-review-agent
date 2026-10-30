# Eval results: full-r1 vs baseline-r1

- **Date:** 2026-10-30 (full-r1 started 2026-10-29, resumed and finished 2026-10-30; baseline-r1 ran on 2026-10-30)
- **Models:** reasoning `openai/gpt-oss-120b`, light `openai/gpt-oss-20b` (Groq), the same for both runs
- **Golden set:** 33 cases (8 sec, 8 arch, 6 test, 6 doc, 5 clean), 32 labelled issues, 6 cases expected to block; 1 repeat per run
- **Git SHAs:**
  - full-r1: 21 cases at `2fee9a1` (2026-10-29). The run stopped on a Groq 431 s retry-after. The other 12 cases (`sec-03`..`sec-08`, `test-01`..`test-06`) were resumed at `8c0f98e` on 2026-10-30.
  - baseline-r1: `8c0f98e`.
  - The only commit between the two SHAs adds 429 logging in `tools/model_client.py`, so agent behaviour is identical.

| Run | Command | Context | Severity cap |
|---|---|---|---|
| full-r1 | `run --cases all --context kg --out evals/runs/full-r1` | knowledge graph from the case files (no vector index) | on |
| baseline-r1 | `run --cases all --context none --no-severity-cap --out evals/runs/baseline-r1` | none | off |

The architecture agent's CRITICAL cap never fired in full-r1 (there's no "downgraded CRITICAL->MEDIUM" line in any log). So in practice the two runs differ only in KG context, plus run-to-run LLM variance.

Raw results and logs are in `evals/runs/{full-r1,baseline-r1}/`, which is git-ignored. Re-score them with `python -m evals.run_evals score <dir> --md`. The tables below were re-scored with no LLM calls after snippet-anchored scoring was added.

**Gate:** block precision **100%** (1/1) and block recall **17%** (1/6), the same in both runs and under both scorings. The only case blocked is `sec-04`, which should block. The five other expected-block cases weren't blocked.

## Strict and snippet-anchored scoring

**Strict** scoring (the primary metric) matches a finding to a labelled issue using the line numbers the agent reported. Agents' reported lines are often wrong (KNOWN_ISSUES #10): in full-r1, 37 of 157 findings are more than 3 lines from the code their own snippet quotes. A correct finding can therefore score as a miss plus a false positive.

**Snippet-anchored** scoring (secondary) first moves each finding to where its snippet sits in the file after the change. That's the first match, with whitespace normalized. The finding keeps its reported lines when the snippet is empty or not found, for example a snippet that quotes removed `-` lines. Matching then works as before, with ±3 lines. 129 of 157 findings in full-r1 and 123 of 151 in baseline-r1 were anchored.

Strict is the number to quote, because it's what a PR comment or "View in code" link would point at today. Snippet-anchored shows how much of the gap is a line-number problem rather than a detection problem. On these runs the gap is small and comes almost entirely from one finding, the `sec-04` CRITICAL.

## Per-category precision and recall

Precision counts findings of MEDIUM or above, excluding duplicates and `acceptable` ones. Recall counts an issue as found at any severity. A finding matches an issue on the same category with line overlap ±3. Each cell gives strict / snippet-anchored.

| Category | Issues | full-r1 recall | baseline recall | full-r1 precision | baseline precision | full-r1 TP / FP (strict) | baseline TP / FP (strict) |
|---|---|---|---|---|---|---|---|
| SECURITY | 8 | 88% / 100% | 100% / 100% | 64% / 73% | 73% / 73% | 7 / 4 | 8 / 3 |
| ARCHITECTURE | 10 | 90% / 90% | 90% / 90% | 21% / 21% | 22% / 23% | 9 / 33 | 9 / 32 |
| TEST_COVERAGE | 6 | 100% / 100% | 100% / 100% | 55% / 55% | 67% / 67% | 6 / 5 | 6 / 3 |
| DOCUMENTATION | 8 | 50% / 50% | 50% / 50% | 0% / 0% | 100% / 100% | 0 / 1 | 3 / 0 |
| **ALL** | 32 | **81% / 84%** | **84% / 84%** | **34% / 35%** | **41% / 42%** | 22 / 43 | 26 / 38 |

**Acceptable findings ≥ MEDIUM, excluded from FP:** full-r1 **26** (22 TEST_COVERAGE, 4 ARCHITECTURE); baseline-r1 **27** (25 TEST_COVERAGE, 2 ARCHITECTURE).

Duplicates ≥ MEDIUM: 9 in each run. LOW/INFO findings: full-r1 57, baseline 51.

## Severity, CRITICAL and blocking

| Metric | full-r1 strict | full-r1 snippet | baseline strict | baseline snippet |
|---|---|---|---|---|
| Matched issues with exact severity | 46% | 48% | 59% | 59% |
| Matched issues within one level | 96% | 96% | 100% | 100% |
| CRITICAL findings | 1 | 1 | 1 | 1 |
| False-CRITICAL rate | 100% (1/1)* | 0% (0/1) | 0% (0/1) | 0% (0/1) |
| Clean cases with any CRITICAL | 0 | 0 | 0 | 0 |
| Block accuracy (33 cases) | 85% | 85% | 85% | 85% |
| Block precision (blocked that should block) | 100% (1/1) | 100% | 100% (1/1) | 100% |
| Block recall (should-block cases blocked) | 17% (1/6) | 17% | 17% (1/6) | 17% |
| False blocks | 0 | 0 | 0 | 0 |
| Missed blocks | 5 | 5 | 5 | 5 |

Blocking comes from the HITL gate, not from line matching, so it's the same under both scorings.

\* The one CRITICAL in full-r1 is a correct finding with a wrong line number. On `sec-04`, the agent reported "Insecure deserialization of client-controlled cookie" at lines 12–15, but its snippet is `pickle.loads(...)`, which is on line 20, where the label is. Snippet-anchored, it matches the labelled issue: SECURITY recall 100% and false-CRITICAL 0%.

Both runs blocked only `sec-04`. They missed the same five blocks: `sec-01`, `sec-02`, `sec-03`, `sec-08` and `arch-05`. In every one of these the issue was found (recall 1/1) but rated HIGH, not CRITICAL. Neither run produced a false block.

## Latency, LLM calls and tokens per case

| Metric | full-r1 | baseline-r1 |
|---|---|---|
| Latency mean | 11.6 s | 12.4 s |
| Latency p50 | 9.2 s | 11.7 s |
| Latency p95 | 29.3 s | 36.6 s |
| LLM calls | 4.7 | 4.6 |
| Prompt tokens | 4,203 | 4,107 |
| Completion tokens (incl. reasoning) | 2,556 | 2,337 |
| Reasoning tokens | 1,447 | 1,240 |
| Groq 429 waits (whole run) | 29 | 26 |
| Model fallbacks (whole run) | 1 | 0 |
| Runs with an agent error | 3 | 1 |

Latency is wall time for one case, cases run one at a time with a 10 s pause between them. It includes Groq free-tier 429 waits (8,000 tokens per minute per model), so it overstates the latency on a paid tier. KG context adds about 100 prompt tokens per case on average.

### Agent errors

These are kept as they are and count in the results. An errored agent contributes no findings.

| Run | Case | Agent | Error |
|---|---|---|---|
| full-r1 | `arch-03-circular-import` | test_coverage | JSON parse failed twice |
| full-r1 | `doc-02-outdated-docstring` | documentation | JSON parse failed twice |
| full-r1 | `sec-07-ssrf` | documentation | timeout (45 s, during a 23 s 429 wait) |
| baseline-r1 | `sec-04-unsafe-deserialization` | documentation | JSON parse failed twice |

The model fallback in full-r1 happened on `sec-06`: one architecture call fell back from `gpt-oss-120b` to `gpt-oss-20b` after repeated per-minute 429s, which is the client's normal fallback path. No daily-limit (TPD/RPD) errors occurred in the resumed part of full-r1 or in baseline-r1. Every 429 logged was a per-minute TPM limit.

## Reading the comparison

- **One repeat per run, so most of the gaps are within noise.** One issue is 12.5 points of recall on SECURITY and DOCUMENTATION (8 issues each), and 10 points on ARCHITECTURE. The SECURITY recall gap is the `sec-04` line-number miss. The DOCUMENTATION precision gap is 0/1 against 3/0 findings at MEDIUM or above. Use `--repeat 3` before drawing conclusions from differences this size.
- **Run-independent results.** These hold in both runs:
  - Recall is high on SECURITY, ARCHITECTURE and TEST_COVERAGE.
  - The architecture agent has low precision: 32–33 false positives, mostly generic "missing input validation" or "uncaught exception" findings.
  - DOCUMENTATION recall is 50%. `doc-04` (README with wrong instructions) gets `[]` every time.
  - No false blocks and no CRITICALs on clean cases.
  - Five of six expected blocks are missed because severity is rated HIGH, not CRITICAL.
- KG context didn't measurably help on this set. Many cases have little cross-file structure, and the mid-function cases get no KG context at all (KNOWN_ISSUES #8).

## Triage: unmatched findings ≥ MEDIUM from full-r1

This list covers all 69 unmatched findings at MEDIUM or above: 43 are counted as FP and 26 match an `acceptable` range.

Verdicts are a first pass from the finding title, its lines and a read of the case file. They're not a re-labelling, and the scores above don't use them. Key:
- **noise**: generic defensive-coding advice with no concrete defect;
- **plausible**: a real but unlabelled issue;
- **wrong line**: a correct finding with an off line number;
- **TC (no tests)**: the test-coverage agent can't see the case's tests;
- **acc**: matches an acceptable range.

| Case | Category | Sev | Lines | Title | Verdict |
|---|---|---|---|---|---|
| arch-01-god-function | SECURITY | MEDIUM | 45-53 | Unvalidated user-supplied email passed to mailer.send | noise |
| arch-01-god-function | TEST_COVERAGE | MEDIUM | 20-35 | Backorder handling for gold customers | acc |
| arch-02-tight-coupling | ARCHITECTURE | MEDIUM | 17-17 | Missing validation for `month` argument | noise |
| arch-02-tight-coupling | TEST_COVERAGE | MEDIUM | 18-22 | build_summary returns None for top_product when no sales | acc |
| arch-03-circular-import | ARCHITECTURE | HIGH | 20-23 | Potential inconsistent state if invoice generation fails | plausible (status is written before `build_invoice`, not atomic) |
| arch-03-circular-import | SECURITY | HIGH | 17-28 | Missing authorization check in complete_order | noise (no auth layer in scope) |
| arch-03-circular-import | ARCHITECTURE | MEDIUM | 22-22 | Possible argument mismatch for `build_invoice` | unverified |
| arch-04-duplicated-logic | TEST_COVERAGE | MEDIUM | 20-32 | Discount calculation truncation and cap edge cases | acc |
| arch-04-duplicated-logic | DOCUMENTATION | MEDIUM | 20-27 | No explanation for discount cap in quote functions | plausible (minor) |
| arch-05-swallowed-exception | TEST_COVERAGE | MEDIUM | 14-21 | Generic exception handling in settle_invoice | acc |
| arch-05-swallowed-exception | ARCHITECTURE | MEDIUM | 30-38 | Missing error handling in bulk settlement loop | plausible (follows from the labelled swallowed exception) |
| arch-06-n-plus-one | ARCHITECTURE | MEDIUM | 13-13 | Potential None value for ticket.opened_at | noise |
| arch-06-n-plus-one | TEST_COVERAGE | MEDIUM | 6-13 | open_ticket_rows handles missing customer data | acc |
| arch-06-n-plus-one | SECURITY | MEDIUM | 5-16 | Missing authorization check leads to sensitive data exposure | noise |
| arch-07-mutable-default | TEST_COVERAGE | MEDIUM | 7-13 | Mutable default argument in extract_tags | acc |
| arch-08-leaky-abstraction | TEST_COVERAGE | MEDIUM | 14-20 | WeatherClient.current does not validate inputs or handle request errors | acc |
| clean-01-slugify | ARCHITECTURE | MEDIUM | 14-14 | Missing validation for non-string title input | noise |
| clean-01-slugify | ARCHITECTURE | MEDIUM | 16-16 | Missing validation for non-positive max_length | noise |
| clean-01-slugify | TEST_COVERAGE | MEDIUM | 12-13 | Negative max_length truncation | TC (no tests) |
| clean-02-extract-constant | ARCHITECTURE | MEDIUM | 7-9 | Missing type validation for `page` and `per_page` arguments | noise |
| clean-02-extract-constant | TEST_COVERAGE | MEDIUM | 10-13 | per_page=0 treated as default instead of clamped to 1 | TC (no tests) |
| clean-03-add-logging | ARCHITECTURE | MEDIUM | 9-12 | Missing validation for supplier_id input | noise |
| clean-03-add-logging | ARCHITECTURE | MEDIUM | 12-12 | Unprotected call to fetch_prices().items() | noise |
| clean-03-add-logging | TEST_COVERAGE | MEDIUM | 10-12 | sync_prices signature now requires string supplier_id | TC (no tests) |
| clean-04-money-value | ARCHITECTURE | MEDIUM | 12-14 | Missing type validation for currency | noise |
| clean-04-money-value | ARCHITECTURE | MEDIUM | 9-21 | Missing type validation for cents | noise |
| clean-04-money-value | TEST_COVERAGE | MEDIUM | 12-15 | Currency code validation in __post_init__ | TC (no tests) |
| clean-04-money-value | TEST_COVERAGE | MEDIUM | 17-21 | Currency mismatch error in __add__ | TC (no tests) |
| doc-01-public-api-no-docstring | ARCHITECTURE | HIGH | 16-16 | Duplicate reference keys in statement are silently overwritten | plausible (`{line.ref: line}` keeps the last duplicate) |
| doc-01-public-api-no-docstring | ARCHITECTURE | MEDIUM | 14-15 | No validation for non-negative tolerance_cents and max_days_apart | noise |
| doc-01-public-api-no-docstring | TEST_COVERAGE | MEDIUM | 14-33 | Duplicate reference keys in statement cause missing matches | acc |
| doc-02-outdated-docstring | TEST_COVERAGE | MEDIUM | 12-20 | reserve raises exception on insufficient stock | acc |
| doc-02-outdated-docstring | TEST_COVERAGE | MEDIUM | 16-24 | reserve returns reservation ID and updates reservations map | acc |
| doc-03-wrong-units | TEST_COVERAGE | MEDIUM | 4-13 | Timeout conversion risk in fetch_json | acc |
| doc-06-magic-numbers | ARCHITECTURE | MEDIUM | 5-9 | Missing validation for `attempt` parameter | noise |
| doc-06-magic-numbers | TEST_COVERAGE | MEDIUM | 5-14 | backoff_delay boundary and status code handling | acc |
| sec-01-sql-injection | TEST_COVERAGE | MEDIUM | 18-26 | search_members vulnerable to SQL injection | acc |
| sec-02-command-injection | ARCHITECTURE | HIGH | 25-26 | Missing request JSON validation leading to unhandled exceptions | acc |
| sec-02-command-injection | ARCHITECTURE | HIGH | 28-28 | No error handling for subprocess.run failures | acc |
| sec-02-command-injection | TEST_COVERAGE | MEDIUM | 27-35 | Unvalidated folder input in backup_folder | acc |
| sec-03-hardcoded-secret | ARCHITECTURE | MEDIUM | 21-21 | Missing validation for charge amount | noise |
| sec-03-hardcoded-secret | ARCHITECTURE | MEDIUM | 31-31 | Missing validation for refund amount | noise |
| sec-03-hardcoded-secret | TEST_COVERAGE | MEDIUM | 27-34 | Optional amount_cents handling in refund | acc |
| sec-04-unsafe-deserialization | SECURITY | CRITICAL | 12-15 | Insecure deserialization of client-controlled cookie | **wrong line** (the snippet is line 20, the labelled `pickle` issue) |
| sec-04-unsafe-deserialization | ARCHITECTURE | MEDIUM | 13-15 | Uncaught exceptions from pickle.loads | noise |
| sec-04-unsafe-deserialization | TEST_COVERAGE | MEDIUM | 9-21 | load_cart handles malformed pickle cookie | acc |
| sec-04-unsafe-deserialization | ARCHITECTURE | MEDIUM | 13-13 | No validation of unpickled data type | noise |
| sec-05-path-traversal | ARCHITECTURE | MEDIUM | 19-19 | Potential unhandled exception from send_file | noise |
| sec-05-path-traversal | TEST_COVERAGE | MEDIUM | 14-19 | Potential path traversal in download_report | acc |
| sec-06-weak-password-hash | ARCHITECTURE | HIGH | 19-23 | Race condition in register allows duplicate email registrations | plausible (check-then-insert) |
| sec-06-weak-password-hash | ARCHITECTURE | MEDIUM | 19-20 | Missing input validation for email and password | noise |
| sec-06-weak-password-hash | TEST_COVERAGE | MEDIUM | 17-23 | register accepts non-string passwords | acc |
| sec-06-weak-password-hash | ARCHITECTURE | MEDIUM | 28-30 | Potential KeyError when accessing user['password_hash'] | noise |
| sec-06-weak-password-hash | ARCHITECTURE | MEDIUM | 21-23 | Uncaught database errors in register and login | noise |
| sec-06-weak-password-hash | TEST_COVERAGE | MEDIUM | 26-31 | login fails with missing password_hash key | acc |
| sec-07-ssrf | ARCHITECTURE | MEDIUM | 16-16 | Missing key validation for request JSON | acc |
| sec-07-ssrf | ARCHITECTURE | MEDIUM | 17-18 | Uncaught exceptions from external HTTP request | acc |
| sec-07-ssrf | TEST_COVERAGE | MEDIUM | 15-18 | Missing or invalid URL in request body | acc |
| sec-07-ssrf | TEST_COVERAGE | MEDIUM | 16-18 | Uncaught exceptions from requests.get | acc |
| sec-08-missing-auth | ARCHITECTURE | MEDIUM | 24-33 | Missing error handling for database delete operation | noise |
| sec-08-missing-auth | TEST_COVERAGE | MEDIUM | 30-38 | Missing test for 404 on non-existent user | acc |
| test-01-new-public-function | ARCHITECTURE | MEDIUM | 4-6 | Missing input validation in invoice_total | noise |
| test-02-changed-branch-logic | ARCHITECTURE | HIGH | 9-18 | Behavioral breaking change in shipping tiers | plausible (the change is intended, but it is a behaviour change) |
| test-02-changed-branch-logic | ARCHITECTURE | MEDIUM | 10-18 | Missing validation for non-finite weight values | noise |
| test-03-error-path-untested | ARCHITECTURE | MEDIUM | 36-40 | Potential off-by-one error in line numbering | noise (`start=2` accounts for the CSV header) |
| test-04-refund-logic | ARCHITECTURE | HIGH | 15-15 | Missing validation for datetime ordering and timezone consistency | plausible (minor; mixing naive and aware datetimes raises) |
| test-05-token-expiry | ARCHITECTURE | HIGH | 9-11 | Missing handling for absent "exp" claim | plausible (`claims["exp"]` raises KeyError) |
| test-05-token-expiry | ARCHITECTURE | MEDIUM | 15-20 | Audience claim handling can raise TypeError when "aud" is None | plausible (`"aud": None` → `in None`) |
| test-06-business-days | ARCHITECTURE | MEDIUM | 5-20 | High cyclomatic complexity in `add_business_days` | noise |

Tally of the 43 counted as FP: noise 27, plausible 9, TC (no tests) 5, wrong line 1, unverified 1. If all 9 plausible ones were added as labels, full-r1 overall precision would rise from 34% to about 48%. The ARCHITECTURE false positives are dominated by validation and exception-handling noise, which is the clearest precision target.

## Known limitations

- **Line numbers are approximate** ([KNOWN_ISSUES #10](../docs/KNOWN_ISSUES.md)). In 37 of 157 full-r1 findings (31 of 149 in baseline-r1), the reported `line_start` is more than 3 lines from where the finding's own snippet sits in the file. Production has the same behaviour (`pipeline/synthesizer.py` notes that the fragment renumbers from 1), so the harness reproduces it as is. Strict scoring counts these as reported. The snippet-anchored columns show the effect: only the `sec-04` CRITICAL changes outcome, and every other metric moves by 3 points or less.
- **Mid-function hunks get no KG context**, even with `--context kg`. This affects `sec-02`, `sec-08`, `arch-03` and `arch-05`. See [KNOWN_ISSUES #8](../docs/KNOWN_ISSUES.md).
- **The architecture agent drops findings that mention "sql" or "database query"**, and `--no-severity-cap` doesn't turn that off. See [KNOWN_ISSUES #9](../docs/KNOWN_ISSUES.md).
- **The test-coverage agent sees no test files.** TEST_COVERAGE findings on new code in non-TC cases are marked `acceptable`, and their count is reported above (26 and 27).
- **One repeat per run, Groq free tier.** LLM variance isn't measured. Latency includes 429 waits, and three agent errors in full-r1 and one in baseline-r1 come from parse failures or a timeout during a rate-limit wait. These count in the results as they are.
