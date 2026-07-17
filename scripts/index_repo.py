import argparse
from pipeline.reindexer import index_repo_from_local_path
from rag.knowledge_graph import kg_path_for_repo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True, help="Path to the repo to index")
    parser.add_argument("--repo", required=True, help="owner/name identifier, e.g. aakash1514/code-review-agent-test")
    parser.add_argument("--kg-output", default=None, help="Override the knowledge graph output path (defaults to a repo-scoped filename)")
    args = parser.parse_args()

    kg_output = args.kg_output or kg_path_for_repo(args.repo)

    print(f"Building knowledge graph for {args.repo}...")
    stats = index_repo_from_local_path(args.path, args.repo, kg_output=kg_output)
    print(f"  {stats['functions']} functions indexed into graph → saved to {kg_output}")
    print(f"  {stats['chunks']} chunks created and indexed into Qdrant collection 'codebase', tagged repo={args.repo!r}")
    print("Done.")


if __name__ == "__main__":
    main()