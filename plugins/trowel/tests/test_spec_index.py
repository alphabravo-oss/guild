import spec_index

SPEC = """---
sealed_by: x
---

# Spec

## Global Invariants

- **GI-001** [from A-001]: never plain HTTP
  and never on loopback

| ID | statement | violation |
|----|-----------|-----------|
| GI-001 | never plain HTTP | a plain listener |

## Requirements

### US-001: Operator scrapes metrics
As an operator I want metrics.

- **AC-001**: GET /metrics returns 200
- **AC-002**: no token returns 401
  - nested detail of AC-002
- see AC-001 for details

FR-010: the setting is called `metrics`.
It is off by default.

```
- **FR-999**: inside a fence
```

## Appendix: Interview Transcript

## A-001 (the answer)
- **FR-500**: in the appendix
"""


def test_defined_ids_in_order_and_references_ignored():
    d = spec_index.index_text(SPEC)
    assert d["ids"] == ["GI-001", "US-001", "AC-001", "AC-002", "FR-010"]
    assert not d["synthetic"]


def test_list_item_keeps_continuation_and_table_row_keeps_header():
    c = spec_index.index_text(SPEC)["clauses"]["GI-001"]
    assert "- **GI-001** [from A-001]: never plain HTTP\n  and never on loopback" in c
    assert "| ID | statement | violation |\n| GI-001 | never plain HTTP | a plain listener |" in c


def test_nested_items_belong_to_their_parent():
    c = spec_index.index_text(SPEC)["clauses"]
    assert "nested detail of AC-002" in c["AC-002"]
    assert "see AC-001" not in c["AC-002"]


def test_heading_clause_stops_at_first_definition():
    c = spec_index.index_text(SPEC)["clauses"]["US-001"]
    assert c.startswith("### US-001") and "As an operator" in c and "AC-001" not in c


def test_line_definition_takes_its_paragraph():
    assert spec_index.index_text(SPEC)["clauses"]["FR-010"] == "FR-010: the setting is called `metrics`.\nIt is off by default."


def test_fences_appendix_and_answer_prefixes_are_not_requirements():
    d = spec_index.index_text(SPEC)
    assert "FR-999" not in d["ids"] and "FR-500" not in d["ids"] and "A-001" not in d["ids"]


def test_answer_prefix_excluded_even_in_body():
    text = "# S\n\n- A-001: an answer cited in the body\n- FR-1: real\n\n## Appendix: Interview Transcript\n\n## A-001 (q)\n"
    assert spec_index.index_text(text)["ids"] == ["FR-1"]


def test_spec_without_ids_is_numbered_by_top_level_items():
    d = spec_index.index_text("# S\n\n- add numbers\n  - with ints\n- subtract numbers\n")
    assert d["synthetic"] and d["ids"] == ["SPEC-1", "SPEC-2"]
    assert d["clauses"]["SPEC-1"] == "- add numbers\n  - with ints"


def test_global_sections_are_kept_verbatim():
    g = spec_index.index_text(SPEC)["global_sections"]
    assert g.startswith("## Global Invariants") and "never plain HTTP" in g and "US-001" not in g
