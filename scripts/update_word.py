"""Select and publish one daily word for a KWGT lock-screen widget."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable
import unicodedata
from zoneinfo import ZoneInfo

try:
    from .duocards_client import DEFAULT_DECK_ID, FetchResult, fetch_all_cards
    from .wiktionary_client import html_to_text, lookup_word
except ImportError:  # Direct execution: python scripts/update_word.py
    from duocards_client import DEFAULT_DECK_ID, FetchResult, fetch_all_cards
    from wiktionary_client import html_to_text, lookup_word


DEFAULT_TIMEZONE = "America/Sao_Paulo"
DEFAULT_HISTORY_LIMIT = 120
DEFAULT_NORMAL_MIN = 5
DEFAULT_NORMAL_MAX = 6
DEFAULT_HARD_MIN = 7


class VocabularyBuilderError(RuntimeError):
    """Raised when no valid daily output can be produced."""


@dataclass(frozen=True)
class Card:
    id: str
    key: str
    word: str
    translation: str
    definition: str
    example: str
    known_count: int
    source_id: str
    s_card_id: str


def normalize_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _string(value: Any) -> str:
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def first_paragraph(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    marked = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    marked = re.sub(r"(?i)</\s*(?:p|div|li|h[1-6])\s*>", "\n\n", marked)
    paragraphs = re.split(r"(?:\r?\n\s*){2,}", marked)
    for paragraph in paragraphs:
        text = html_to_text(paragraph)
        if text:
            return text
    return ""


def normalize_card(raw: dict[str, Any]) -> Card | None:
    word = _string(raw.get("front"))
    known_count = raw.get("knownCount")
    if not word or not isinstance(known_count, int) or isinstance(known_count, bool):
        return None

    s_card = raw.get("sCard")
    theory = s_card.get("theory") if isinstance(s_card, dict) else None
    definition = first_paragraph(
        theory.get("theoryEn") if isinstance(theory, dict) else None
    )
    return Card(
        id=_string(raw.get("id")),
        key=normalize_key(word),
        word=word,
        translation=_string(raw.get("back")),
        definition=definition,
        example=_string(raw.get("hint")),
        known_count=known_count,
        source_id=_string(raw.get("sourceId")),
        s_card_id=_string(raw.get("sCardId")),
    )


def _dedupe_sort_key(card: Card) -> tuple[Any, ...]:
    complete = bool(card.definition and card.example)
    populated = sum(bool(value) for value in (card.definition, card.example, card.translation))
    content_length = len(card.definition) + len(card.example) + len(card.translation)
    return (-int(complete), -card.known_count, -populated, -content_length, card.id)


def deduplicate_cards(raw_cards: Iterable[dict[str, Any]]) -> list[Card]:
    grouped: dict[str, list[Card]] = {}
    for raw in raw_cards:
        if not isinstance(raw, dict):
            continue
        card = normalize_card(raw)
        if card is not None:
            grouped.setdefault(card.key, []).append(card)
    return [min(group, key=_dedupe_sort_key) for _, group in sorted(grouped.items())]


def is_hard_day(day: date) -> bool:
    return day.weekday() in {2, 6}  # Wednesday and Sunday


def eligible_cards(
    cards: Iterable[Card],
    *,
    hard_day: bool,
    normal_min: int = DEFAULT_NORMAL_MIN,
    normal_max: int = DEFAULT_NORMAL_MAX,
    hard_min: int = DEFAULT_HARD_MIN,
) -> list[Card]:
    if hard_day:
        return [card for card in cards if card.known_count >= hard_min]
    return [card for card in cards if normal_min <= card.known_count <= normal_max]


def load_history(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VocabularyBuilderError(f"Invalid history file {path}: {error}") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise VocabularyBuilderError(f"Invalid history schema in {path}")
    items = payload.get("items")
    if not isinstance(items, list):
        raise VocabularyBuilderError(f"Invalid history items in {path}")
    result: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            raise VocabularyBuilderError(f"Invalid history entry in {path}")
        required = ("date", "word", "key", "mode")
        if any(not isinstance(item.get(field), str) for field in required):
            raise VocabularyBuilderError(f"Invalid history entry fields in {path}")
        result.append({field: item[field] for field in required})
    return result


def _daily_order_key(card: Card, day: date, mode: str) -> str:
    return hashlib.sha256(f"{day.isoformat()}|{mode}|{card.key}".encode()).hexdigest()


def candidate_order(
    cards: list[Card],
    history: list[dict[str, str]],
    *,
    day: date,
    hard_day: bool,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> list[Card]:
    mode = "hard" if hard_day else "normal"
    same_day_key = next(
        (
            item["key"]
            for item in reversed(history)
            if item["date"] == day.isoformat() and item["mode"] == mode
        ),
        None,
    )
    by_key = {card.key: card for card in cards}
    ordered: list[Card] = []
    if same_day_key in by_key:
        ordered.append(by_key[same_day_key])

    recent = history[-history_limit:] if history_limit else []
    recent_keys = {item["key"] for item in recent}
    unseen = [card for card in cards if card.key not in recent_keys]
    unseen.sort(key=lambda card: _daily_order_key(card, day, mode))
    ordered.extend(card for card in unseen if card.key != same_day_key)

    last_seen: dict[str, int] = {}
    for index, item in enumerate(history):
        last_seen[item["key"]] = index
    seen = [card for card in cards if card.key in recent_keys and card.key != same_day_key]
    seen.sort(key=lambda card: (last_seen.get(card.key, -1), _daily_order_key(card, day, mode)))
    ordered.extend(seen)
    return ordered


def enrich_card(card: Card) -> tuple[str, str, dict[str, str]] | None:
    entry = lookup_word(card.word)
    if entry is None or not entry.definition or not entry.example:
        return None
    return entry.definition, entry.example, entry.to_dict()


def choose_enriched_card(
    ordered_candidates: Iterable[Card], *, max_lookups: int
) -> tuple[Card, str, str, dict[str, str]]:
    if max_lookups < 1:
        raise ValueError("max_lookups must be positive")
    attempts = 0
    for card in ordered_candidates:
        attempts += 1
        if attempts > max_lookups:
            break
        enriched = enrich_card(card)
        if enriched is not None:
            definition, example, provenance = enriched
            return card, definition, example, provenance
    raise VocabularyBuilderError(
        f"No candidate produced both a definition and an example after {attempts if attempts <= max_lookups else max_lookups} lookups"
    )


def build_word_payload(
    card: Card,
    definition: str,
    example: str,
    provenance: dict[str, str],
    *,
    day: date,
    hard_day: bool,
) -> dict[str, Any]:
    mode = "hard" if hard_day else "normal"
    source_name = provenance.get("source") or provenance.get("definition_source") or ""
    combined_source = (
        "DuoCards"
        if not source_name or source_name == "DuoCards"
        else f"DuoCards + {source_name}"
    )
    payload: dict[str, Any] = {
        "word": card.word,
        "definition": definition,
        "example": example,
        "translation": card.translation,
        "known_count": card.known_count,
        "hard_day": hard_day,
        "hard_day_label": "hard day" if hard_day else "",
        "mode": mode,
        "date": day.isoformat(),
        "source": combined_source,
        "vocabulary_source": "DuoCards",
        "definition_source": provenance.get("source")
        or provenance.get("definition_source", ""),
        "definition_source_url": provenance.get("source_url")
        or provenance.get("definition_source_url", ""),
        "attribution": provenance.get("attribution", ""),
        "license_name": provenance.get("license_name", ""),
        "license_url": provenance.get("license_url", ""),
    }
    validate_word_payload(payload)
    return payload


def validate_word_payload(payload: dict[str, Any]) -> None:
    for field in ("word", "definition", "example", "date", "mode"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            raise VocabularyBuilderError(f"Output field {field!r} cannot be empty")
    if payload["mode"] not in {"normal", "hard"}:
        raise VocabularyBuilderError("Output mode must be normal or hard")
    if not isinstance(payload.get("known_count"), int) or isinstance(
        payload.get("known_count"), bool
    ):
        raise VocabularyBuilderError("Output known_count must be an integer")
    if not isinstance(payload.get("hard_day"), bool):
        raise VocabularyBuilderError("Output hard_day must be a boolean")


def updated_history(
    history: list[dict[str, str]],
    card: Card,
    *,
    day: date,
    hard_day: bool,
    history_limit: int,
) -> dict[str, Any]:
    mode = "hard" if hard_day else "normal"
    items = [item for item in history if item["date"] != day.isoformat()]
    items.append(
        {"date": day.isoformat(), "word": card.word, "key": card.key, "mode": mode}
    )
    if history_limit:
        items = items[-history_limit:]
    return {"version": 1, "items": items}


def _txt_field(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("~", " ")).strip()


def build_word_txt(payload: dict[str, Any]) -> str:
    return "~".join(
        _txt_field(str(payload[field]))
        for field in ("word", "definition", "example", "hard_day_label")
    ) + "\n"


def _write_many_atomically(files: dict[Path, str]) -> None:
    temporary: dict[Path, Path] = {}
    try:
        for destination, content in files.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="\n",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(content)
                temporary[destination] = Path(handle.name)
        for destination, temp_path in temporary.items():
            os.replace(temp_path, destination)
    finally:
        for temp_path in temporary.values():
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _load_cards_input(path: Path) -> FetchResult:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VocabularyBuilderError(f"Invalid cards input {path}: {error}") from error
    cards = payload.get("cards") if isinstance(payload, dict) else None
    if not isinstance(cards, list):
        raise VocabularyBuilderError(f"Cards input {path} has no cards list")
    return FetchResult(
        cards=cards,
        release_id=payload.get("release_id"),
        pages=int(payload.get("pages", 0)),
    )


def resolve_day(value: str | None, timezone_name: str) -> date:
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise VocabularyBuilderError(f"Invalid --date value {value!r}") from error
    try:
        return datetime.now(ZoneInfo(timezone_name)).date()
    except Exception as error:
        raise VocabularyBuilderError(f"Invalid timezone {timezone_name!r}") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deck-id", default=os.environ.get("DUOCARDS_DECK_ID", DEFAULT_DECK_ID))
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument("--date", help="Override local date (YYYY-MM-DD).")
    parser.add_argument("--mode", choices=("auto", "normal", "hard"), default="auto")
    parser.add_argument("--normal-min", type=int, default=DEFAULT_NORMAL_MIN)
    parser.add_argument("--normal-max", type=int, default=DEFAULT_NORMAL_MAX)
    parser.add_argument("--hard-min", type=int, default=DEFAULT_HARD_MIN)
    parser.add_argument("--history-limit", type=int, default=DEFAULT_HISTORY_LIMIT)
    parser.add_argument("--max-lookups", type=int, default=25)
    parser.add_argument("--history", type=Path, default=Path("data/history.json"))
    parser.add_argument("--word-json", type=Path, default=Path("word.json"))
    parser.add_argument("--word-txt", type=Path, default=Path("word.txt"))
    parser.add_argument("--cards-input", type=Path, help="Use a private snapshot instead of the API.")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.normal_min > args.normal_max:
        raise VocabularyBuilderError("--normal-min cannot exceed --normal-max")
    if args.history_limit < 1:
        raise VocabularyBuilderError("--history-limit must be positive")

    day = resolve_day(args.date, args.timezone)
    hard = is_hard_day(day) if args.mode == "auto" else args.mode == "hard"
    result = _load_cards_input(args.cards_input) if args.cards_input else fetch_all_cards(args.deck_id)
    cards = deduplicate_cards(result.cards)
    eligible = eligible_cards(
        cards,
        hard_day=hard,
        normal_min=args.normal_min,
        normal_max=args.normal_max,
        hard_min=args.hard_min,
    )
    if not eligible:
        raise VocabularyBuilderError("The selected knownCount bucket has no cards")

    history = load_history(args.history)
    ordered = candidate_order(
        eligible,
        history,
        day=day,
        hard_day=hard,
        history_limit=args.history_limit,
    )
    card, definition, example, provenance = choose_enriched_card(
        ordered, max_lookups=args.max_lookups
    )
    payload = build_word_payload(
        card,
        definition,
        example,
        provenance,
        day=day,
        hard_day=hard,
    )
    history_payload = updated_history(
        history,
        card,
        day=day,
        hard_day=hard,
        history_limit=args.history_limit,
    )

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    _write_many_atomically(
        {
            args.word_json: json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            args.word_txt: build_word_txt(payload),
            args.history: json.dumps(history_payload, ensure_ascii=False, indent=2) + "\n",
        }
    )
    print(
        json.dumps(
            {
                "date": payload["date"],
                "mode": payload["mode"],
                "word": payload["word"],
                "known_count": payload["known_count"],
                "eligible_cards": len(eligible),
                "total_cards": len(cards),
                "duocards_pages": result.pages,
                "duocards_release_id": result.release_id,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
