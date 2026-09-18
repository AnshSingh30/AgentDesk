from app.runtime.formatter import citations_footer, format_for_whatsapp, markdown_to_whatsapp, split_message


def test_bold_and_headings() -> None:
    out = markdown_to_whatsapp("# Leave Policy\n\nYou get **12 days** of *casual* leave.")
    assert out.startswith("*Leave Policy*")
    assert "*12 days*" in out
    assert "_casual_" in out


def test_links_and_lists() -> None:
    out = markdown_to_whatsapp("* Read [the policy](https://example.com/p)\n* Ask HR\n\n---\n")
    assert "- Read the policy (https://example.com/p)" in out
    assert "---" not in out


def test_table_renders_monospace_and_caps_rows() -> None:
    rows = "\n".join(f"| Dept{i} | {i * 10} |" for i in range(20))
    md = f"| Department | Count |\n|---|---|\n{rows}"
    out = markdown_to_whatsapp(md)
    assert out.startswith("```")
    assert "Department" in out and "more rows" in out
    assert out.count("\n") < 22


def test_wide_table_becomes_bullets() -> None:
    md = "| a | b | c | d | e |\n|---|---|---|---|---|\n| 1 | 2 | 3 | 4 | 5 |"
    out = markdown_to_whatsapp(md)
    assert out.startswith("- a: 1; b: 2")


def test_split_message_paragraph_boundaries() -> None:
    text = "\n\n".join(f"Paragraph {i} " + "x" * 900 for i in range(8))
    parts = split_message(text, limit=4000)
    assert len(parts) >= 2
    assert all(len(p) <= 4096 for p in parts)
    assert parts[0].endswith(f"(1/{len(parts)})")


def test_split_hard_when_no_boundaries() -> None:
    parts = split_message("y" * 9000, limit=4000)
    assert len(parts) == 3


def test_citations_footer_and_format() -> None:
    cites = [
        {"filename": "Leave Policy.pdf", "page_start": 4},
        {"filename": "Employee Handbook.pdf", "page_start": 12},
        {"filename": "Leave Policy.pdf", "page_start": 4},
    ]
    assert citations_footer(cites) == "📄 Sources: Leave Policy (p.4), Employee Handbook (p.12)"
    msgs = format_for_whatsapp("**Answer** here [1]", cites)
    assert msgs[0].startswith("*Answer*") and "📄 Sources" in msgs[0]
