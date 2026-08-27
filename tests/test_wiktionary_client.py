"""Tests for the small, dependency-free Wiktionary client."""

from __future__ import annotations

import json
import unittest
from urllib.error import HTTPError

from scripts.wiktionary_client import (
    html_to_text,
    lookup_word,
    parse_definition_response,
)


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


class HtmlToTextTests(unittest.TestCase):
    def test_removes_markup_decodes_entities_and_collapses_whitespace(self) -> None:
        value = (
            "<span>done&nbsp;routinely &amp; with "
            "<strong>little</strong>\n care</span>"
        )

        self.assertEqual(
            html_to_text(value),
            "done routinely & with little care",
        )

    def test_preserves_spaces_across_html_separators(self) -> None:
        self.assertEqual(html_to_text("one<br>two<li>three</li>"), "one two three")


class ParseDefinitionResponseTests(unittest.TestCase):
    def test_keeps_first_sense_even_when_a_later_sense_has_an_example(self) -> None:
        payload = {
            "en": [
                {
                    "partOfSpeech": "<i>adjective</i>",
                    "definitions": [
                        {"definition": "<span>A broad first sense.</span>"},
                        {
                            "definition": "<span>The preferred&nbsp;sense.</span>",
                            "parsedExamples": [
                                {
                                    "example": (
                                        "<em>She</em> chose it &amp; used it carefully."
                                    )
                                }
                            ],
                        },
                    ],
                },
                {
                    "partOfSpeech": "noun",
                    "definitions": [
                        {
                            "definition": "A later sense.",
                            "examples": ["A later example."],
                        }
                    ],
                },
            ]
        }

        entry = parse_definition_response("ice cream/tea", payload)

        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.definition, "A broad first sense.")
        self.assertEqual(entry.example, "")
        self.assertEqual(entry.part_of_speech, "adjective")
        self.assertEqual(
            entry.source_url,
            "https://en.wiktionary.org/wiki/ice_cream%2Ftea",
        )


class LookupWordTests(unittest.TestCase):
    def test_rejects_non_positive_attempt_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "attempts must be positive"):
            lookup_word("word", attempts=0)

    def test_returns_none_immediately_for_404(self) -> None:
        calls: list[str] = []
        sleeps: list[float] = []

        def open_url(request: object, *, timeout: float) -> _FakeResponse:
            del timeout
            url = request.full_url  # type: ignore[attr-defined]
            calls.append(url)
            raise HTTPError(url, 404, "Not Found", {}, None)

        result = lookup_word(
            "missing",
            api_root="https://example.test/definition",
            attempts=3,
            open_url=open_url,
            sleep=sleeps.append,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, ["https://example.test/definition/missing"])
        self.assertEqual(sleeps, [])

    def test_normalizes_and_url_encodes_word_path(self) -> None:
        requests: list[tuple[str, float]] = []

        def open_url(request: object, *, timeout: float) -> _FakeResponse:
            requests.append(
                (request.full_url, timeout)  # type: ignore[attr-defined]
            )
            return _FakeResponse({"en": []})

        result = lookup_word(
            "  café   au/lait?  ",
            api_root="https://example.test/definition/",
            timeout=7.5,
            attempts=1,
            open_url=open_url,
            sleep=lambda _: self.fail("a successful request must not sleep"),
        )

        self.assertIsNone(result)
        self.assertEqual(
            requests,
            [("https://example.test/definition/caf%C3%A9%20au%2Flait%3F", 7.5)],
        )


if __name__ == "__main__":
    unittest.main()
