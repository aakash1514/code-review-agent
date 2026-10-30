"""Tag handling for blog posts."""
import re

TAG_RE = re.compile(r"#(\w{2,30})")


def extract_tags(text: str, tags: list[str] = []) -> list[str]:
    """Return the hashtags in `text` (lower-cased, without '#'), appended to `tags`."""
    for match in TAG_RE.finditer(text):
        tag = match.group(1).lower()
        if tag not in tags:
            tags.append(tag)
    return tags


def tag_post(post: dict) -> dict:
    """Return a copy of the post with a `tags` list built from its title and body."""
    tags = extract_tags(post["title"])
    tags = extract_tags(post["body"], tags)
    return {**post, "tags": tags}
