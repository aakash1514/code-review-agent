from rag.knowledge_graph import KnowledgeGraph
from rag.indexer import CodebaseIndexer


class SmartRetriever:
    def __init__(self, knowledge_graph: KnowledgeGraph, indexer: CodebaseIndexer):
        self.kg = knowledge_graph
        self.indexer = indexer

    def retrieve_for_diff(
        self,
        repo: str,
        changed_file: str,
        diff_snippet: str,
        changed_fn_names: list[str],
    ) -> dict:
        """
        repo is now required — passed straight through to indexer.search()
        so vector search only ever returns chunks from THIS repo. Without
        it, search() (shared Qdrant collection across all installed repos)
        has no way to scope results, which is exactly what caused another
        repo's source code to show up as "similar patterns" in testing.
        """
        context = {
            "changed_file": changed_file,
            "call_chain": [],
            "dependents": [],
            "similar_patterns": [],
        }

        for fn_name in changed_fn_names:
            fn_key = self.kg.find_fn_key_by_name_and_file(changed_file, fn_name)
            if not fn_key:
                continue

            for called_key in self.kg.get_call_chain(fn_key, depth=2):
                node = self.kg.nodes.get(called_key)
                if node:
                    context["call_chain"].append({"fn_key": called_key, "file": node.file_path})

            for dep_key in self.kg.get_dependents(fn_key):
                node = self.kg.nodes.get(dep_key)
                if node:
                    context["dependents"].append({"fn_key": dep_key, "file": node.file_path})

        similar = self.indexer.search(query_text=diff_snippet, repo=repo, limit=5)
        context["similar_patterns"] = [
            {"name": s["name"], "file": s["file_path"], "snippet": s["source"][:300]}
            for s in similar
        ]

        return context

    def build_agent_context_string(self, context: dict, max_chars: int = 8000) -> str:
        parts = [f"Changed file: {context['changed_file']}"]

        if context["call_chain"]:
            parts.append("Calls (functions this code depends on):")
            for c in context["call_chain"][:10]:
                parts.append(f"  - {c['fn_key']}")

        if context["dependents"]:
            parts.append("Called by (impact radius — code that depends on this):")
            for d in context["dependents"][:10]:
                parts.append(f"  - {d['fn_key']}")

        if context["similar_patterns"]:
            parts.append("Similar patterns elsewhere in codebase:")
            for s in context["similar_patterns"][:5]:
                parts.append(f"  - {s['name']} ({s['file']}):\n{s['snippet']}")

        result = "\n".join(parts)
        return result[:max_chars]