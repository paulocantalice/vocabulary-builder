from scripts.update_word import (
    first_paragraph,
    split_deck_ids,
    parse_weekdays,
)

assert first_paragraph(
    "<p>First paragraph.</p><p>Second paragraph.</p>"
) == "First paragraph."

assert split_deck_ids(
    "A\nB\nA;C,B"
) == ["A", "B", "C"]

assert parse_weekdays("2,6") == {2, 6}

print("Tests passed.")
