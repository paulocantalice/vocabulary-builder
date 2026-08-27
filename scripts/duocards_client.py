"""Small, dependency-free client for the DuoCards deck GraphQL query.

The operation name in the URL is intentional. DuoCards currently returns HTTP
403 for the same payload sent to ``/graphql`` without ``?cardsQuery``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import time
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "https://api.duocards.com/graphql?cardsQuery"
SOURCE_API_URL = "https://api.duocards.com/graphql?sourceQuery"
DEFAULT_DECK_ID = "RGVjazoxYzIyYzgxMy1jNjJkLTQ0NGUtYTMzMi1lZjBhMDhmMjdhYjU="
DEFAULT_PAGE_SIZE = 100

CARDS_QUERY = """\
query cardsQuery(
  $count: Int!
  $cursor: String
  $deckId: ID!
  $search: String
  $cardState: CardState
) {
  node(id: $deckId) {
    __typename
    ... on Deck {
      cards(
        first: $count
        after: $cursor
        search: $search
        cardState: $cardState
      ) {
        edges {
          node {
            id
            sCardId
            sBackId
            sourceId
            front
            back
            hint
            waiting
            knownCount
            source {
              kind
              course
              id
            }
            sCard {
              theory {
                sCardId
                theory
                theoryIsChecked
                theoryNative
                theoryNativeIsChecked
                theoryEn
                lang
                langNative
                canEdit
              }
              id
            }
            svg {
              flatId
              url
              id
            }
            __typename
          }
          cursor
        }
        pageInfo {
          endCursor
          hasNextPage
        }
      }
      id
    }
  }
}
"""

SOURCE_QUERY = """\
query sourceQuery(
  $uriOrId: String!
  $backLang: String
  $deckId: ID
) {
  sourceByUriOrId(uriOrId: $uriOrId) {
    id
    uri
    name(langNative: $backLang)
    lang
    kind
    course
    sCards(backLang: $backLang) {
      id
      front
      hint
      sourceId
      back(langNative: $backLang) {
        sBackId
        value
        translated
        id
      }
      theory(langNative: $backLang) {
        sCardId
        theory
        theoryIsChecked
        theoryNative
        theoryNativeIsChecked
        theoryEn
        lang
        langNative
        canEdit
      }
      isInMyDeck(deckId: $deckId)
    }
  }
}
"""


class DuoCardsError(RuntimeError):
    """Raised when DuoCards returns an invalid or unsuccessful response."""


@dataclass(frozen=True)
class FetchResult:
    cards: list[dict[str, Any]]
    release_id: str | None
    pages: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


OpenUrl = Callable[..., Any]


def _read_http_error(error: HTTPError) -> str:
    try:
        return error.read().decode("utf-8", errors="replace")[:2_000]
    except Exception:
        return ""


def _post_graphql(
    variables: dict[str, Any],
    *,
    query: str = CARDS_QUERY,
    api_url: str = API_URL,
    timeout: float = 30,
    attempts: int = 3,
    open_url: OpenUrl = urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    payload = json.dumps(
        {"query": query, "variables": variables},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "vocabulary-builder/1.0",
    }

    last_error: Exception | None = None
    for attempt in range(attempts):
        request = Request(api_url, data=payload, headers=headers, method="POST")
        try:
            with open_url(request, timeout=timeout) as response:
                raw = response.read()
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict):
                raise DuoCardsError("DuoCards returned a non-object JSON response")
            errors = result.get("errors")
            if errors:
                raise DuoCardsError(
                    "DuoCards GraphQL error: "
                    + json.dumps(errors, ensure_ascii=False)[:2_000]
                )
            return result
        except HTTPError as error:
            last_error = error
            if error.code not in {408, 429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                detail = _read_http_error(error)
                suffix = f": {detail}" if detail else ""
                raise DuoCardsError(f"DuoCards HTTP {error.code}{suffix}") from error
            retry_after = error.headers.get("Retry-After") if error.headers else None
            try:
                delay = float(retry_after) if retry_after is not None else 2**attempt
                if delay < 0:
                    raise ValueError
            except (TypeError, ValueError):
                delay = 2**attempt
        except (URLError, TimeoutError) as error:
            last_error = error
            if attempt + 1 >= attempts:
                raise DuoCardsError(f"DuoCards connection failed: {error}") from error
            delay = 2**attempt
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DuoCardsError("DuoCards returned invalid JSON") from error

        sleep(delay)

    raise DuoCardsError(f"DuoCards request failed: {last_error}")


def fetch_source(
    source_id_or_uri: str,
    deck_id: str,
    *,
    back_lang: str | None = None,
    api_url: str = SOURCE_API_URL,
    timeout: float = 30,
    attempts: int = 3,
    open_url: OpenUrl = urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Fetch one public source/list and its shared-card metadata."""

    response = _post_graphql(
        {
            "uriOrId": source_id_or_uri,
            "backLang": back_lang,
            "deckId": deck_id,
        },
        query=SOURCE_QUERY,
        api_url=api_url,
        timeout=timeout,
        attempts=attempts,
        open_url=open_url,
        sleep=sleep,
    )
    data = response.get("data")
    source = data.get("sourceByUriOrId") if isinstance(data, dict) else None
    if not isinstance(source, dict):
        raise DuoCardsError(f"DuoCards source {source_id_or_uri!r} was not found")
    return source


def fetch_all_cards(
    deck_id: str,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = 1_000,
    api_url: str = API_URL,
    timeout: float = 30,
    attempts: int = 3,
    page_delay: float = 0.2,
    open_url: OpenUrl = urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchResult:
    """Fetch every card in a deck, rejecting broken/repeated pagination."""

    deck_id = deck_id.strip()
    if not deck_id:
        raise ValueError("deck_id cannot be empty")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    if page_delay < 0:
        raise ValueError("page_delay cannot be negative")

    cards: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    release_id: str | None = None

    for page_number in range(1, max_pages + 1):
        response = _post_graphql(
            {
                "count": page_size,
                "cursor": cursor,
                "deckId": deck_id,
                "search": "",
                "cardState": None,
            },
            api_url=api_url,
            timeout=timeout,
            attempts=attempts,
            open_url=open_url,
            sleep=sleep,
        )
        extensions = response.get("extensions")
        if isinstance(extensions, dict) and isinstance(extensions.get("releaseId"), str):
            release_id = extensions["releaseId"]

        data = response.get("data")
        node = data.get("node") if isinstance(data, dict) else None
        if not isinstance(node, dict) or node.get("__typename") != "Deck":
            raise DuoCardsError("The supplied ID did not resolve to a DuoCards Deck")
        connection = node.get("cards")
        if not isinstance(connection, dict):
            raise DuoCardsError("DuoCards response is missing the cards connection")
        edges = connection.get("edges")
        page_info = connection.get("pageInfo")
        if not isinstance(edges, list) or not isinstance(page_info, dict):
            raise DuoCardsError("DuoCards returned malformed pagination data")

        for edge in edges:
            if not isinstance(edge, dict) or not isinstance(edge.get("node"), dict):
                raise DuoCardsError("DuoCards returned a malformed card edge")
            cards.append(edge["node"])

        has_next_page = page_info.get("hasNextPage")
        if has_next_page is False:
            return FetchResult(cards=cards, release_id=release_id, pages=page_number)
        if has_next_page is not True:
            raise DuoCardsError("DuoCards returned a non-boolean hasNextPage value")

        next_cursor = page_info.get("endCursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            raise DuoCardsError("DuoCards reported another page without an endCursor")
        if next_cursor in seen_cursors or next_cursor == cursor:
            raise DuoCardsError(f"DuoCards repeated pagination cursor {next_cursor!r}")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        if page_delay:
            sleep(page_delay)

    raise DuoCardsError(f"DuoCards pagination exceeded {max_pages} pages")


def iter_theory(cards: Iterable[dict[str, Any]]) -> Iterable[str]:
    """Yield non-empty ``theoryEn`` strings from raw card objects."""

    for card in cards:
        s_card = card.get("sCard")
        theory = s_card.get("theory") if isinstance(s_card, dict) else None
        value = theory.get("theoryEn") if isinstance(theory, dict) else None
        if isinstance(value, str) and value.strip():
            yield value
