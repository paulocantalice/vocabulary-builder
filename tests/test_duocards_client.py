"""Contract tests for :mod:`scripts.duocards_client`.

The tests deliberately exercise the HTTP boundary through the injectable
``open_url`` callable.  No real DuoCards request is made.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from scripts import duocards_client


DECK_ID = "RGVjazoxYzIyYzgxMy1jNjJkLTQ0NGUtYTMzMi1lZjBhMDhmMjdhYjU="


class FakeResponse:
    """Minimal context-managed response returned by ``urlopen``."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


class QueueOpener:
    """Record requests and return queued GraphQL responses in order."""

    def __init__(self, *payloads: dict[str, Any]) -> None:
        self._payloads = list(payloads)
        self.requests: list[Any] = []
        self.timeouts: list[float] = []

    def __call__(self, request: Any, *, timeout: float) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if not self._payloads:
            raise AssertionError("The client made more requests than expected")
        return FakeResponse(self._payloads.pop(0))


def graphql_page(
    cards: list[dict[str, Any]],
    *,
    has_next_page: bool,
    end_cursor: str | None,
    release_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "data": {
            "node": {
                "__typename": "Deck",
                "id": DECK_ID,
                "cards": {
                    "edges": [{"node": card, "cursor": str(index)} for index, card in enumerate(cards)],
                    "pageInfo": {
                        "endCursor": end_cursor,
                        "hasNextPage": has_next_page,
                    },
                },
            }
        }
    }
    if release_id is not None:
        payload["extensions"] = {"releaseId": release_id}
    return payload


class FetchAllCardsTests(unittest.TestCase):
    def test_default_url_preserves_literal_cards_query_suffix(self) -> None:
        opener = QueueOpener(
            graphql_page([], has_next_page=False, end_cursor=None)
        )

        duocards_client.fetch_all_cards(
            DECK_ID,
            open_url=opener,
            sleep=lambda _seconds: None,
        )

        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0]
        self.assertEqual(request.full_url, duocards_client.API_URL)
        self.assertTrue(request.full_url.endswith("/graphql?cardsQuery"))
        self.assertNotIn("?cardsQuery=", request.full_url)

        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["variables"]["deckId"], DECK_ID)
        self.assertIsNone(body["variables"]["cursor"])
        self.assertEqual(body["variables"]["count"], 100)

    def test_fetches_two_pages_and_passes_end_cursor(self) -> None:
        opener = QueueOpener(
            graphql_page(
                [{"id": "card-1", "front": "first"}],
                has_next_page=True,
                end_cursor="cursor-1",
                release_id="release-1",
            ),
            graphql_page(
                [{"id": "card-2", "front": "second"}],
                has_next_page=False,
                end_cursor="cursor-2",
                release_id="release-2",
            ),
        )

        result = duocards_client.fetch_all_cards(
            DECK_ID,
            open_url=opener,
            sleep=lambda _seconds: None,
        )

        self.assertEqual([card["id"] for card in result.cards], ["card-1", "card-2"])
        self.assertEqual(result.pages, 2)
        self.assertEqual(result.release_id, "release-2")
        self.assertEqual(len(opener.requests), 2)

        first_body = json.loads(opener.requests[0].data.decode("utf-8"))
        second_body = json.loads(opener.requests[1].data.decode("utf-8"))
        self.assertIsNone(first_body["variables"]["cursor"])
        self.assertEqual(second_body["variables"]["cursor"], "cursor-1")

    def test_trims_deck_id_and_pauses_between_successful_pages(self) -> None:
        opener = QueueOpener(
            graphql_page([], has_next_page=True, end_cursor="cursor-1"),
            graphql_page([], has_next_page=False, end_cursor=None),
        )
        sleeps: list[float] = []

        duocards_client.fetch_all_cards(
            f"  {DECK_ID}  ",
            page_delay=0.25,
            open_url=opener,
            sleep=sleeps.append,
        )

        first_body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertEqual(first_body["variables"]["deckId"], DECK_ID)
        self.assertEqual(sleeps, [0.25])

    def test_rejects_missing_cursor_when_another_page_is_reported(self) -> None:
        opener = QueueOpener(
            graphql_page([], has_next_page=True, end_cursor=None)
        )

        with self.assertRaisesRegex(
            duocards_client.DuoCardsError,
            "another page without an endCursor",
        ):
            duocards_client.fetch_all_cards(
                DECK_ID,
                open_url=opener,
                sleep=lambda _seconds: None,
            )

        self.assertEqual(len(opener.requests), 1)

    def test_rejects_repeated_cursor(self) -> None:
        opener = QueueOpener(
            graphql_page([], has_next_page=True, end_cursor="cursor-1"),
            graphql_page([], has_next_page=True, end_cursor="cursor-1"),
        )

        with self.assertRaisesRegex(
            duocards_client.DuoCardsError,
            "repeated pagination cursor 'cursor-1'",
        ):
            duocards_client.fetch_all_cards(
                DECK_ID,
                open_url=opener,
                sleep=lambda _seconds: None,
            )

        self.assertEqual(len(opener.requests), 2)

    def test_surfaces_graphql_errors_without_retrying(self) -> None:
        opener = QueueOpener(
            {"errors": [{"message": "Deck access denied"}], "data": None}
        )

        with self.assertRaisesRegex(
            duocards_client.DuoCardsError,
            "DuoCards GraphQL error.*Deck access denied",
        ):
            duocards_client.fetch_all_cards(
                DECK_ID,
                attempts=3,
                open_url=opener,
                sleep=lambda _seconds: None,
            )

        self.assertEqual(len(opener.requests), 1)

    def test_rejects_malformed_card_edge(self) -> None:
        payload = graphql_page([], has_next_page=False, end_cursor=None)
        payload["data"]["node"]["cards"]["edges"] = [None]
        opener = QueueOpener(payload)

        with self.assertRaisesRegex(duocards_client.DuoCardsError, "malformed card edge"):
            duocards_client.fetch_all_cards(DECK_ID, open_url=opener)

    def test_rejects_non_positive_attempt_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "attempts must be positive"):
            duocards_client.fetch_all_cards(DECK_ID, attempts=0)


class TheoryParsingTests(unittest.TestCase):
    def test_iter_theory_yields_only_non_empty_nested_theory_en_strings(self) -> None:
        cards = [
            {"sCard": {"theory": {"theoryEn": "first definition"}}},
            {"sCard": None},
            {"sCard": {"theory": None}},
            {"sCard": {"theory": {"theoryEn": "   "}}},
            {"sCard": {"theory": {"theoryEn": 123}}},
            {"sCard": {"theory": {"theoryEn": "second definition"}}},
        ]

        self.assertEqual(
            list(duocards_client.iter_theory(cards)),
            ["first definition", "second definition"],
        )


if __name__ == "__main__":
    unittest.main()
