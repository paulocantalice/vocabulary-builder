"""Unit tests for the daily word selection and serialization rules."""

from __future__ import annotations

from datetime import date, timedelta
import unittest

from scripts import update_word


def make_card(
    word: str,
    known_count: int = 5,
    *,
    card_id: str | None = None,
    translation: str = "translation",
    definition: str = "A complete definition.",
    example: str = "A complete example.",
) -> update_word.Card:
    return update_word.Card(
        id=card_id or f"id-{word}",
        key=update_word.normalize_key(word),
        word=word,
        translation=translation,
        definition=definition,
        example=example,
        known_count=known_count,
        source_id=f"source-{word}",
        s_card_id=f"scard-{word}",
    )


def make_raw_card(
    word: object,
    known_count: object,
    *,
    card_id: str,
    translation: object = "translation",
    definition: object = "<p>A complete definition.</p>",
    example: object = "A complete example.",
) -> dict[str, object]:
    theory = {"theoryEn": definition} if definition is not None else None
    return {
        "id": card_id,
        "front": word,
        "back": translation,
        "hint": example,
        "knownCount": known_count,
        "sourceId": f"source-{card_id}",
        "sCardId": f"scard-{card_id}",
        "sCard": {"theory": theory},
    }


def history_item(day: date, word: str, mode: str = "normal") -> dict[str, str]:
    return {
        "date": day.isoformat(),
        "word": word,
        "key": update_word.normalize_key(word),
        "mode": mode,
    }


class NormalizeAndDedupeTests(unittest.TestCase):
    def test_normalize_key_applies_nfkc_casefold_and_whitespace(self) -> None:
        self.assertEqual(
            update_word.normalize_key("  ＣＡＦÉ\tStraße \n"),
            "café strasse",
        )

    def test_normalize_card_extracts_first_paragraph_and_cleans_strings(self) -> None:
        raw = make_raw_card(
            "  Perfunctory\n glance  ",
            12,
            card_id="card-1",
            translation="  superficial\t glance ",
            definition=(
                "<p>Done <strong>routinely</strong> &amp; without care.</p>"
                "<p>This paragraph must not be selected.</p>"
            ),
            example="  He gave it\n a perfunctory glance. ",
        )

        card = update_word.normalize_card(raw)

        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.word, "Perfunctory glance")
        self.assertEqual(card.key, "perfunctory glance")
        self.assertEqual(card.translation, "superficial glance")
        self.assertEqual(card.definition, "Done routinely & without care.")
        self.assertEqual(card.example, "He gave it a perfunctory glance.")
        self.assertEqual(card.known_count, 12)

    def test_normalize_card_rejects_missing_word_and_non_integer_count(self) -> None:
        invalid = (
            make_raw_card("", 5, card_id="empty-word"),
            make_raw_card("word", "5", card_id="string-count"),
            make_raw_card("word", True, card_id="boolean-count"),
        )

        for raw in invalid:
            with self.subTest(raw=raw):
                self.assertIsNone(update_word.normalize_card(raw))

    def test_dedupe_prefers_complete_card_before_higher_known_count(self) -> None:
        raw_cards = [
            make_raw_card(
                " ＰＥＲＦＵＮＣＴＯＲＹ ",
                99,
                card_id="incomplete-high-count",
                definition="<p>Definition only.</p>",
                example="",
            ),
            make_raw_card(
                "perfunctory",
                5,
                card_id="complete-low-count",
                definition="<p>Complete but lower count.</p>",
                example="Complete example.",
            ),
            make_raw_card(
                "Perfunctory",
                12,
                card_id="complete-high-count",
                definition="<p>Complete and higher count.</p>",
                example="Complete example.",
            ),
        ]

        cards = update_word.deduplicate_cards(raw_cards)

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].id, "complete-high-count")

    def test_dedupe_uses_more_populated_content_after_completeness_and_count(self) -> None:
        raw_cards = [
            make_raw_card(
                "cumbersome",
                8,
                card_id="without-translation",
                translation="",
            ),
            make_raw_card(
                " CUMBERSOME ",
                8,
                card_id="with-translation",
                translation="trabalhoso",
            ),
        ]

        cards = update_word.deduplicate_cards(raw_cards)

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].id, "with-translation")


class BucketAndCalendarTests(unittest.TestCase):
    def test_default_normal_and_hard_ranges_include_their_boundaries(self) -> None:
        cards = [make_card(f"word-{count}", count) for count in range(4, 9)]

        normal = update_word.eligible_cards(cards, hard_day=False)
        hard = update_word.eligible_cards(cards, hard_day=True)

        self.assertEqual([card.known_count for card in normal], [5, 6])
        self.assertEqual([card.known_count for card in hard], [7, 8])

    def test_custom_product_ranges_are_normal_5_through_25_and_hard_26_plus(self) -> None:
        cards = [make_card(f"word-{count}", count) for count in (4, 5, 25, 26, 80)]

        normal = update_word.eligible_cards(
            cards, hard_day=False, normal_min=5, normal_max=25, hard_min=26
        )
        hard = update_word.eligible_cards(
            cards, hard_day=True, normal_min=5, normal_max=25, hard_min=26
        )

        self.assertEqual([card.known_count for card in normal], [5, 25])
        self.assertEqual([card.known_count for card in hard], [26, 80])

    def test_only_wednesday_and_sunday_are_hard_days(self) -> None:
        monday = date(2026, 8, 24)
        expected = {
            0: False,
            1: False,
            2: True,
            3: False,
            4: False,
            5: False,
            6: True,
        }

        for offset, is_hard in expected.items():
            day = monday + timedelta(days=offset)
            with self.subTest(day=day):
                self.assertEqual(update_word.is_hard_day(day), is_hard)


class HistoryAndCandidateTests(unittest.TestCase):
    def test_same_date_reexecution_keeps_the_existing_word_first(self) -> None:
        today = date(2026, 8, 27)
        alpha = make_card("alpha")
        beta = make_card("beta")
        history = [
            history_item(today - timedelta(days=1), "beta"),
            history_item(today, "alpha"),
        ]

        ordered = update_word.candidate_order(
            [beta, alpha], history, day=today, hard_day=False
        )

        self.assertEqual(ordered[0].key, alpha.key)

    def test_same_date_history_update_is_idempotent(self) -> None:
        today = date(2026, 8, 27)
        selected = make_card("perfunctory")
        history = [
            history_item(today - timedelta(days=1), "cumbersome"),
            history_item(today, "old-selection"),
        ]

        first = update_word.updated_history(
            history,
            selected,
            day=today,
            hard_day=False,
            history_limit=120,
        )
        second = update_word.updated_history(
            first["items"],
            selected,
            day=today,
            hard_day=False,
            history_limit=120,
        )

        self.assertEqual(first, second)
        self.assertEqual(
            [item["date"] for item in second["items"]].count(today.isoformat()), 1
        )

    def test_history_is_trimmed_to_the_latest_120_entries(self) -> None:
        start = date(2026, 1, 1)
        history = [
            history_item(start + timedelta(days=index), f"word-{index:03d}")
            for index in range(120)
        ]
        selected = make_card("new-word")

        payload = update_word.updated_history(
            history,
            selected,
            day=start + timedelta(days=120),
            hard_day=False,
            history_limit=120,
        )

        self.assertEqual(len(payload["items"]), 120)
        self.assertNotIn("word-000", {item["key"] for item in payload["items"]})
        self.assertEqual(payload["items"][-1]["key"], "new-word")

    def test_word_outside_recent_120_is_preferred_to_recent_word(self) -> None:
        start = date(2026, 1, 1)
        history = [
            history_item(start + timedelta(days=index), f"word-{index:03d}")
            for index in range(121)
        ]
        outside_window = make_card("word-000")
        inside_window = make_card("word-001")

        ordered = update_word.candidate_order(
            [inside_window, outside_window],
            history,
            day=date(2027, 1, 1),
            hard_day=False,
            history_limit=120,
        )

        self.assertEqual(ordered[0].key, outside_window.key)

    def test_when_every_word_is_recent_fallback_uses_least_recently_seen(self) -> None:
        start = date(2026, 8, 1)
        history = [
            history_item(start, "alpha"),
            history_item(start + timedelta(days=1), "beta"),
            history_item(start + timedelta(days=2), "alpha"),
            history_item(start + timedelta(days=3), "charlie"),
        ]
        cards = [make_card("alpha"), make_card("charlie"), make_card("beta")]

        ordered = update_word.candidate_order(
            cards,
            history,
            day=date(2026, 8, 27),
            hard_day=False,
            history_limit=120,
        )

        self.assertEqual([card.key for card in ordered], ["beta", "alpha", "charlie"])


class SerializationAndPayloadTests(unittest.TestCase):
    def test_word_txt_removes_delimiters_and_collapses_all_whitespace(self) -> None:
        payload = {
            "word": "  per~functory\n",
            "definition": "done ~ routinely\r\n with care",
            "example": " He\tgave\n it. ",
            "hard_day_label": " hard~day ",
        }

        rendered = update_word.build_word_txt(payload)

        self.assertEqual(
            rendered,
            "per functory~done routinely with care~He gave it.~hard day\n",
        )
        self.assertNotIn("\r", rendered)
        self.assertEqual(rendered.count("~"), 3)

    def test_payload_contains_normal_mode_and_wiktionary_provenance(self) -> None:
        card = make_card("perfunctory", 6, translation="superficial")
        provenance = {
            "source": "Wiktionary",
            "source_url": "https://en.wiktionary.org/wiki/perfunctory",
            "attribution": "English Wiktionary contributors",
            "license_name": "CC BY-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        }

        payload = update_word.build_word_payload(
            card,
            "Done routinely and without care.",
            "He gave it a perfunctory glance.",
            provenance,
            day=date(2026, 8, 27),
            hard_day=False,
        )

        self.assertEqual(payload["word"], "perfunctory")
        self.assertEqual(payload["translation"], "superficial")
        self.assertEqual(payload["known_count"], 6)
        self.assertEqual(payload["mode"], "normal")
        self.assertFalse(payload["hard_day"])
        self.assertEqual(payload["hard_day_label"], "")
        self.assertEqual(payload["date"], "2026-08-27")
        self.assertEqual(payload["source"], "DuoCards + Wiktionary")
        self.assertEqual(payload["definition_source"], "Wiktionary")
        self.assertEqual(payload["definition_source_url"], provenance["source_url"])
        self.assertEqual(payload["license_name"], "CC BY-SA 4.0")

    def test_payload_marks_hard_day_and_does_not_duplicate_duocards_source(self) -> None:
        card = make_card("cumbersome", 7, translation="trabalhoso")
        provenance = {
            "definition_source": "DuoCards",
            "definition_source_url": "https://app.duocards.com/",
            "attribution": "DuoCards card data",
            "license_name": "",
            "license_url": "",
        }

        payload = update_word.build_word_payload(
            card,
            card.definition,
            card.example,
            provenance,
            day=date(2026, 8, 30),
            hard_day=True,
        )

        self.assertTrue(payload["hard_day"])
        self.assertEqual(payload["hard_day_label"], "hard day")
        self.assertEqual(payload["mode"], "hard")
        self.assertEqual(payload["source"], "DuoCards")

    def test_payload_validation_rejects_boolean_known_count(self) -> None:
        payload = {
            "word": "perfunctory",
            "definition": "A definition.",
            "example": "An example.",
            "date": "2026-08-27",
            "mode": "normal",
            "known_count": True,
            "hard_day": False,
        }

        with self.assertRaises(update_word.VocabularyBuilderError):
            update_word.validate_word_payload(payload)

    def test_payload_validation_rejects_empty_required_fields_and_bad_types(self) -> None:
        base = {
            "word": "perfunctory",
            "definition": "A definition.",
            "example": "An example.",
            "date": "2026-08-27",
            "mode": "normal",
            "known_count": 6,
            "hard_day": False,
        }

        invalid_payloads = []
        for field in ("word", "definition", "example", "date"):
            invalid_payloads.append({**base, field: "  "})
        invalid_payloads.extend(
            (
                {**base, "mode": "unexpected"},
                {**base, "known_count": "6"},
                {**base, "hard_day": "false"},
            )
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(update_word.VocabularyBuilderError):
                    update_word.validate_word_payload(payload)


if __name__ == "__main__":
    unittest.main()
