"""Read English definitions and examples from Wikimedia's Wiktionary API."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from html.parser import HTMLParser
import json
import os
import re
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


API_ROOT = "https://en.wiktionary.org/api/rest_v1/page/definition"
LICENSE_NAME = "CC BY-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
ATTRIBUTION = "English Wiktionary contributors"


class WiktionaryError(RuntimeError):
    """Raised for an invalid or unsuccessful Wiktionary response."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() in {"br", "div", "li", "p"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"div", "li", "p"}:
            self.parts.append(" ")

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.parts)).strip()


@dataclass(frozen=True)
class DictionaryEntry:
    definition: str
    example: str
    part_of_speech: str
    source: str
    source_url: str
    attribution: str = ATTRIBUTION
    license_name: str = LICENSE_NAME
    license_url: str = LICENSE_URL

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


OpenUrl = Callable[..., Any]


def html_to_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return re.sub(r"\s+", " ", value).strip()
    return parser.text()


def _first_example(definition: dict[str, Any]) -> str:
    parsed = definition.get("parsedExamples")
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                text = html_to_text(item.get("example"))
                if text:
                    return text
    examples = definition.get("examples")
    if isinstance(examples, list):
        for item in examples:
            text = html_to_text(item)
            if text:
                return text
    return ""


def parse_definition_response(word: str, payload: Any) -> DictionaryEntry | None:
    """Return Wiktionary's first English sense without crossing into another sense.

    A later sense may have a convenient example but a different meaning. The
    caller rejects an entry whose first sense has no example and tries another
    vocabulary candidate instead.
    """

    if not isinstance(payload, dict):
        raise WiktionaryError("Wiktionary returned a non-object JSON response")
    english = payload.get("en")
    if not isinstance(english, list):
        return None

    source_url = "https://en.wiktionary.org/wiki/" + quote(word.replace(" ", "_"), safe="")
    for meaning in english:
        if not isinstance(meaning, dict):
            continue
        part_of_speech = html_to_text(meaning.get("partOfSpeech"))
        definitions = meaning.get("definitions")
        if not isinstance(definitions, list):
            continue
        for item in definitions:
            if not isinstance(item, dict):
                continue
            definition = html_to_text(item.get("definition"))
            if not definition:
                continue
            return DictionaryEntry(
                definition=definition,
                example=_first_example(item),
                part_of_speech=part_of_speech,
                source="Wiktionary",
                source_url=source_url,
            )
    return None


def lookup_word(
    word: str,
    *,
    api_root: str = API_ROOT,
    timeout: float = 30,
    attempts: int = 3,
    open_url: OpenUrl = urlopen,
    sleep: Callable[[float], None] = time.sleep,
    user_agent: str | None = None,
) -> DictionaryEntry | None:
    normalized = re.sub(r"\s+", " ", word).strip()
    if not normalized:
        raise ValueError("word cannot be empty")
    if attempts < 1:
        raise ValueError("attempts must be positive")
    url = api_root.rstrip("/") + "/" + quote(normalized, safe="")
    agent = user_agent or os.environ.get(
        "WIKIMEDIA_USER_AGENT",
        "VocabularyBuilder/1.0 (personal lock-screen vocabulary project)",
    )

    for attempt in range(attempts):
        request = Request(url, headers={"Accept": "application/json", "User-Agent": agent})
        try:
            with open_url(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return parse_definition_response(normalized, payload)
        except HTTPError as error:
            if error.code == 404:
                return None
            if error.code not in {408, 429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                raise WiktionaryError(f"Wiktionary HTTP {error.code} for {normalized!r}") from error
            retry_after = error.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
        except (URLError, TimeoutError) as error:
            if attempt + 1 >= attempts:
                raise WiktionaryError(
                    f"Wiktionary connection failed for {normalized!r}: {error}"
                ) from error
            delay = 2**attempt
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WiktionaryError(
                f"Wiktionary returned invalid JSON for {normalized!r}"
            ) from error
        sleep(delay)

    return None
