# Known issues

Deferred fixes and open questions, each with when it should be fixed.

1. **Call-graph key collision within one file.** `rag/knowledge_graph.py` keys nodes as `file::function`, not `file::Class.function`, so two classes in the same file with a same-named method share one node and the second overwrites the first. Calls between classes in different files are scoped correctly. Pinned by `tests/test_knowledge_graph.py::test_same_named_methods_in_one_file_get_separate_nodes`.
   **Fix when:** next change to the knowledge graph. It needs a key format change, a matching update to the retriever's `find_fn_key_by_name_and_file` lookup, and a reindex of every repo.

2. **Large patches are skipped, not reviewed.** The retrieved RAG context is capped at 8000 characters (`SmartRetriever.build_agent_context_string`). Patches over `MAX_PATCH_CHARS` (default 20000) are now skipped and listed as "patch too large", which keeps prompts under Groq's request size limit and avoids HTTP 413, but leaves those changes unreviewed.
   **Fix when:** large hand-written changes start getting skipped in practice. Chunk a large patch by hunk or function and review the chunks, instead of skipping the file.

3. **PR review behaviour on non-default branches.** Pushes to the default branch trigger a reindex. PRs targeting other branches are still reviewed against the default branch's index. The intended behaviour isn't decided yet.
   **Fix when:** the spec is written. Don't change this until then.

4. **Override decisions keep only the latest one.** `review_jobs.override_action/override_by/override_at` are overwritten by each approve, block or clear, so there's no history of who changed a decision and when.
   **Fix when:** before more than one reviewer uses the dashboard, or when an audit trail is required. Add an append-only `override_events` table and keep the current columns as the latest-state cache.

5. **dompurify vulnerability inside monaco-editor.** The diff viewer's Monaco editor (loaded from the CDN by `@monaco-editor/react`) bundles dompurify 3.4.15, which has a known advisory. The dashboard has no direct dompurify dependency to bump.
   **Fix when:** a monaco-editor release ships dompurify 3.4.17 or later. Then bump the Monaco version the loader uses.

6. **Monaco "TextModel got disposed before DiffEditorWidget model got reset".** Leaving the Code tab logs this error to the browser console. It's cosmetic; no functional impact has been seen.
   **Fix when:** convenient. Reset the diff editor's model before unmounting, or wait for an upstream fix in `@monaco-editor/react`.

7. **Auth state is per instance or SQLite-only.** The repo read-access cache and repo visibility cache are in-process; sessions, WebSocket tickets and login handoff codes live in the local SQLite file; the WebSocket connection manager is in memory. The OAuth `state` values in `api/auth.py` (`_pending_states`) are in-process memory too, so a login whose callback lands on a different instance than `/auth/login` fails with "invalid or expired OAuth state". A second backend instance would see different caches and a different database.
   **Fix when:** before running more than one backend instance (AWS Phase 8.7). Move all of these to a shared store such as Redis or Postgres, the OAuth state store included (SQLite also works for it while every instance shares one database).

8. **No changed functions for hunks that start mid-function.** `tools/code_tools.strip_diff_to_source` turns a patch into a source fragment for `ast.parse`. When a hunk starts inside a function body, the fragment doesn't parse, so no changed functions are extracted. Those files get no knowledge-graph context and no complexity metrics. Found by the eval harness: `sec-02`, `sec-08`, `arch-03` and `arch-05` in `evals/cases/` get no KG context even with `--context kg`.
   **Fix when:** next change to retrieval context. Parse the full file after the change (`get_full_file_content`) and map hunk line ranges to the functions they touch.

9. **The architecture agent drops every finding that mentions SQL.** `agents/architecture_agent._strip_security_leaks` removes ANY finding whose title or description contains "sql" or "database query". Legitimate architecture findings, such as an N+1 query loop, are dropped with them. The eval's `--no-severity-cap` doesn't disable this filter.
   **Fix when:** the evals show it costing architecture recall (watch `arch-06-n-plus-one`). Narrow the keyword list to injection wording, or rely on the snippet-pattern check alone.

10. **Fixed in PR #5 (re-anchoring).** **Agent-reported line ranges are often wrong.** The agents number lines in the source fragment they're shown, not in the file, and they guess, so `line_start`/`line_end` often don't point at the code the finding quotes. In the eval run `full-r1`, 37 of 157 findings are more than 3 lines from their own snippet. The wrong lines reach PR review comments and the dashboard's "View in code" link. The evals report a secondary snippet-anchored score for this reason (`evals/RESULTS.md`).
   **Fixed:** `pipeline/runner.run_agents_parallel` now re-anchors every agent's findings with `tools/code_tools.reanchor_finding_lines`. Each finding moves to where its snippet sits among the patch's added and context lines (whitespace-insensitive, nearest to the reported line when the snippet appears more than once).
   **Remaining limitation:** a snippet that quotes removed lines, is truncated or paraphrased, or is empty isn't found, so that finding keeps the model's guessed lines.

11. **A critical SSRF or path traversal doesn't auto-block.** `agents/security_agent._ceil_context_dependent_classes` caps CRITICAL to HIGH for findings worded as SSRF, path traversal, weak hashing, XSS, CSRF or insecure randomness, unless they also qualify for a canonical floor class. How bad these are depends on what's reachable, which one file doesn't show; in eval `gate-r2` the `gpt-oss-20b` fallback rated `sec-07`'s SSRF CRITICAL and blocked a case labelled HIGH. The cost: an SSRF that reaches cloud metadata, or a traversal that reads secrets, goes to human review as HIGH and doesn't block the merge.
   **Fix when:** the agents get deployment context (network reachability, what the read path can reach). Then let the model's CRITICAL stand when that context supports it.

12. **Deferred: which model produced each agent result isn't recorded.** `agents/model_client` falls through to the next model when a call fails (for example a 429), but the `AgentReport` doesn't say which model answered. The eval harness only counts `[model_client]` log lines per case (`model_fallbacks`). In `gate-r2`, `sec-07`'s CRITICAL SSRF came from the `gpt-oss-20b` fallback for the security agent, which the run record shows only through the log.
   **Fix when:** next change to `model_client`. Return the model name with the response, store it on `AgentReport`, and count fallbacks per agent in the eval report.
