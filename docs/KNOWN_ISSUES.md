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
