from tools.code_tools import extract_functions, strip_diff_to_source

# Shaped like a real `git diff` / GitHub PR patch: header lines, a hunk
# header with a function-name hint, context lines, removals and additions.
DIFF = """\
diff --git a/app/pricing.py b/app/pricing.py
index 3f2a1b0..9c4d7e2 100644
--- a/app/pricing.py
+++ b/app/pricing.py
@@ -1,9 +1,16 @@ import math
 import math


 def calculate_discount(price, rate):
-    return price * rate
+    if rate < 0 or rate > 1:
+        raise ValueError("rate must be between 0 and 1")
+    return round(price * rate, 2)


-def legacy_total(items):
-    return sum(items)
+async def fetch_total(client, order_id):
+    order = await client.get(order_id)
+    return sum(item.price for item in order.items)
+
+
+class Cart:
+    def add(self, item, qty=1):
+        self.items.append((item, qty))
"""


def test_strip_diff_drops_metadata_and_removed_lines():
    source = strip_diff_to_source(DIFF)

    for marker in ("diff --git", "index 3f2a", "--- a/", "+++ b/", "@@ "):
        assert marker not in source
    assert "return price * rate" not in source      # removed line
    assert "legacy_total" not in source              # removed function
    assert "return round(price * rate, 2)" in source  # added line
    assert source.splitlines()[0] == "import math"   # context line, prefix stripped


def test_stripped_diff_parses_and_yields_new_functions():
    functions = {fn.name: fn for fn in extract_functions(strip_diff_to_source(DIFF))}

    assert set(functions) == {"calculate_discount", "fetch_total", "add"}
    assert functions["calculate_discount"].args == ["price", "rate"]
    assert functions["fetch_total"].is_async is True
    assert functions["add"].args == ["self", "item", "qty"]
    discount = functions["calculate_discount"]
    assert discount.source.startswith("def calculate_discount(price, rate):")
    assert discount.end_line - discount.start_line == 3


def test_raw_diff_without_stripping_yields_nothing():
    # The silent failure strip_diff_to_source exists to prevent.
    assert extract_functions(DIFF) == []


def test_hunk_starting_mid_function_degrades_to_empty():
    diff = "@@ -10,3 +10,3 @@ def f():\n         x = 1\n-        return x\n+        return x + 1\n"
    assert extract_functions(strip_diff_to_source(diff)) == []


def test_no_newline_marker_does_not_break_parsing():
    diff = DIFF + "\\ No newline at end of file\n"
    names = {fn.name for fn in extract_functions(strip_diff_to_source(diff))}
    assert names == {"calculate_discount", "fetch_total", "add"}


def test_no_newline_markers_after_removed_and_added_sides_are_dropped():
    # Old file ended without a newline and the PR appends to it, so git puts
    # a marker after the removed line, and another after the new last line.
    diff = (
        "@@ -1,2 +1,5 @@\n"
        " def first():\n"
        "-    return 1\n"
        "\\ No newline at end of file\n"
        "+    return 1\n"
        "+\n"
        "+def second():\n"
        "+    return 2\n"
        "\\ No newline at end of file\n"
    )

    source = strip_diff_to_source(diff)

    assert "No newline" not in source
    assert source == "def first():\n    return 1\n\ndef second():\n    return 2"
    assert [fn.name for fn in extract_functions(source)] == ["first", "second"]
