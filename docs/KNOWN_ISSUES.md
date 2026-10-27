# Known issues

Deferred fixes and open questions, each with when it should be fixed. Items 8 and 9 were found by the test suite and are pinned by strict `xfail` tests, so fixing them makes those tests fail until the `xfail` marker is removed.

1. **Call-graph key collision within one file.** `rag/knowledge_graph.py` keys nodes as `file::function`, not `file::Class.function`, so two classes in the same file with a same-named method share one node and the second overwrites the first. Calls between classes in different files are scoped correctly. Pinned by `tests/test_knowledge_graph.py::test_same_named_methods_in_one_file_get_separate_nodes`.
   **Fix when:** next change to the knowledge graph. It needs a key format change, a matching update to the retriever's `find_fn_key_by_name_and_file` lookup, and a reindex of every repo.

2. **Groq 413 on large prompts.** The retrieved RAG context is capped at 8000 characters (`SmartRetriever.build_agent_context_string`), but the diff passed to each agent isn't capped at all. A large single-file diff can exceed Groq's request size and fail that agent with HTTP 413.
   **Fix when:** before reviewing repos with large generated or vendored files, or as soon as a 413 shows up in the logs. Cap or chunk the diff per agent call and say in the review when a file was truncated.

3. **PR review behaviour on non-default branches.** Pushes to the default branch trigger a reindex. PRs targeting other branches are still reviewed against the default branch's index. The intended behaviour isn't decided yet.
   **Fix when:** the spec is written. Don't change this until then.

4. **Override decisions keep only the latest one.** `review_jobs.override_action/override_by/override_at` are overwritten by each approve, block or clear, so there's no history of who changed a decision and when.
   **Fix when:** before more than one reviewer uses the dashboard, or when an audit trail is required. Add an append-only `override_events` table and keep the current columns as the latest-state cache.

5. **dompurify vulnerability inside monaco-editor.** The diff viewer's Monaco editor (loaded from the CDN by `@monaco-editor/react`) bundles dompurify 3.4.15, which has a known advisory. The dashboard has no direct dompurify dependency to bump.
   **Fix when:** a monaco-editor release ships dompurify 3.4.17 or later. Then bump the Monaco version the loader uses.

6. **Monaco "TextModel got disposed before DiffEditorWidget model got reset".** Leaving the Code tab logs this error to the browser console. It's cosmetic; no functional impact has been seen.
   **Fix when:** convenient. Reset the diff editor's model before unmounting, or wait for an upstream fix in `@monaco-editor/react`.

7. **Auth state is per instance or SQLite-only.** The repo read-access cache and repo visibility cache are in-process; sessions, WebSocket tickets and login handoff codes live in the local SQLite file; the WebSocket connection manager is in memory. A second backend instance would see different caches and a different database.
   **Fix when:** before running more than one backend instance (AWS Phase 8.7). Move these to a shared store such as Redis or Postgres.

8. **"\ No newline at end of file" breaks function extraction.** `tools/code_tools.strip_diff_to_source` keeps git's `\ No newline at end of file` marker as a source line, so `ast.parse` fails and `extract_functions` silently returns nothing. GitHub includes this marker in PR patches, so a changed file without a trailing newline gets no function names, which weakens retrieval and finding grouping for that file. Pinned by `tests/test_code_tools.py::test_no_newline_marker_does_not_break_parsing`.
   **Fix when:** next change to `code_tools`. Skip lines starting with `\ ` in `strip_diff_to_source`.

9. **De-duplication drops distinct findings that have no snippet.** `Finding.dedup_key` is file + category + snippet hash. The snippet defaults to an empty string when an agent leaves it out, so two unrelated findings in the same file and category (for example "Missing docstring for foo" and "... for bar") collapse into one, and a real finding is silently lost. Pinned by `tests/test_synthesizer.py::test_distinct_findings_without_snippets_are_both_kept`.
   **Fix when:** before relying on review completeness. Include the title, or the line range, in the key when the snippet is empty.
