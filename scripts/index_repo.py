import argparse
import os
from rag.chunker import chunk_file
from rag.knowledge_graph import KnowledgeGraph, kg_path_for_repo
from rag.indexer import CodebaseIndexer

EXCLUDED_DIRS = {".git", "venv", "env", ".venv", "node_modules", "__pycache__", "dist", "build"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True, help="Path to the repo to index")
    parser.add_argument("--repo", required=True, help="owner/name identifier, e.g. aakash1514/code-review-agent-test")
    parser.add_argument("--kg-output", default=None, help="Override the knowledge graph output path (defaults to a repo-scoped filename)")
    args = parser.parse_args()

    kg_output = args.kg_output or kg_path_for_repo(args.repo)

    print(f"Building knowledge graph for {args.repo}...")
    kg = KnowledgeGraph()
    kg.build_from_repo(args.path)
    kg.save(kg_output)
    print(f"  {len(kg.nodes)} functions indexed into graph → saved to {kg_output}")

    print("Chunking + embedding for Qdrant...")
    all_chunks = []
    for root, dirs, files in os.walk(args.path):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

        for fname in files:
            if not fname.endswith((".py", ".js", ".ts")):
                continue
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, args.path).replace(os.sep, "/")
            try:
                source = open(full_path, encoding="utf-8").read()
            except UnicodeDecodeError:
                continue
            all_chunks.extend(chunk_file(rel_path, source))

    print(f"  {len(all_chunks)} chunks created")

    indexer = CodebaseIndexer()
    indexer.index_chunks(all_chunks, repo=args.repo)
    print(f"  Indexed into Qdrant collection 'codebase', tagged repo={args.repo!r}")
    print("Done.")


if __name__ == "__main__":
    main()