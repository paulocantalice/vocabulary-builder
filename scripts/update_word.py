#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import html
import json
import os
import random
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

API_URL = "https://api.duocards.com/graphql"

ROOT = Path(__file__).resolve().parents[1]
HISTORY_FILE = ROOT / "data" / "history.json"
WORD_JSON = ROOT / "word.json"
WORD_TXT = ROOT / "word.txt"

PAGE_SIZE = 100

QUERY = r"""
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
            front
            back
            hint
            knownCount
            sCard {
              theory {
                theoryEn
              }
            }
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
    id
  }
}
"""


def clean_text(value: str | None) -> str:
    """Turn DuoCards' light HTML/rich text into clean plain text."""
    if not value:
        return ""

    text = html.unescape(str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Keep paragraph breaks while removing HTML.
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*/\s*p\s*>", "\n\n", text)
    text = re.sub(r"(?i)<\s*p(?:\s[^>]*)?>", "", text)
    text = re.sub(r"<[^>]+>", "", text)

    text = "\n".join(
        re.sub(r"[ \t]+", " ", line).strip()
        for line in text.split("\n")
    )
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def first_paragraph(value: str | None) -> str:
    """Return only the first paragraph of DuoCards' English theory text."""
    text = clean_text(value)
    if not text:
        return ""

    paragraphs = [
        p.strip()
        for p in re.split(r"\n\s*\n", text)
        if p.strip()
    ]
    first = paragraphs[0] if paragraphs else text

    return re.sub(r"\s*\n\s*", " ", first).strip()


def split_deck_ids(raw: str) -> list[str]:
    """
    Accept one Deck ID per line, or comma/semicolon-separated.
    Duplicate IDs are removed automatically.
    """
    ids = [
        item.strip()
        for item in re.split(r"[\n,;]+", raw or "")
        if item.strip()
    ]
    return list(dict.fromkeys(ids))


def graphql_request(deck_id: str, cursor: str | None) -> dict[str, Any]:
    payload = {
        "query": QUERY,
        "variables": {
            "count": PAGE_SIZE,
            "cursor": cursor,
            "deckId": deck_id,
            "search": "",
            "cardState": None,
        },
    }

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Vocabulary-builder/3.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)

    if result.get("errors"):
        raise RuntimeError(
            "DuoCards GraphQL error: "
            + json.dumps(result["errors"], ensure_ascii=False)
        )

    return result


def fetch_deck(deck_id: str, deck_number: int) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        result = graphql_request(deck_id, cursor)
        node = ((result.get("data") or {}).get("node"))

        if not node:
            raise RuntimeError("Deck not found for this Deck ID.")

        connection = node.get("cards")
        if not connection:
            raise RuntimeError("DuoCards returned no cards connection.")

        for edge in connection.get("edges", []):
            card = edge.get("node") or {}

            theory_en = (
                (((card.get("sCard") or {}).get("theory") or {}).get("theoryEn"))
            )

            cards.append(
                {
                    "id": card.get("id"),
                    "word": clean_text(card.get("front")),
                    "translation": clean_text(card.get("back")),
                    "definition": first_paragraph(theory_en),
                    "example": clean_text(card.get("hint")),
                    "known_count": int(card.get("knownCount") or 0),
                    "deck_number": deck_number,
                }
            )

        page_info = connection.get("pageInfo") or {}

        if not page_info.get("hasNextPage"):
            break

        cursor = page_info.get("endCursor")
        if not cursor:
            raise RuntimeError(
                "DuoCards says another page exists but returned no cursor."
            )

    return cards


def fetch_all_decks(deck_ids: list[str]) -> list[dict[str, Any]]:
    all_cards: list[dict[str, Any]] = []
    errors: list[str] = []

    for index, deck_id in enumerate(deck_ids, start=1):
        try:
            deck_cards = fetch_deck(deck_id, index)
            all_cards.extend(deck_cards)
            print(f"Deck {index}: {len(deck_cards)} cards.")
        except Exception as exc:
            errors.append(f"Deck {index}: {exc}")
            print(f"WARNING: Deck {index} failed: {exc}", file=sys.stderr)

    if not all_cards:
        raise RuntimeError(
            "No DuoCards deck could be read. " + " | ".join(errors)
        )

    # A word may exist in several old decks. Keep only one copy.
    # Prefer: definition+example present -> higher knownCount -> richer text.
    best_by_word: dict[str, dict[str, Any]] = {}

    for card in all_cards:
        key = card["word"].casefold().strip()
        if not key:
            continue

        score = (
            int(bool(card["definition"])) + int(bool(card["example"])),
            card["known_count"],
            len(card["definition"]) + len(card["example"]),
        )

        current = best_by_word.get(key)

        if current is None:
            best_by_word[key] = card
            continue

        current_score = (
            int(bool(current["definition"])) + int(bool(current["example"])),
            current["known_count"],
            len(current["definition"]) + len(current["example"]),
        )

        if score > current_score:
            best_by_word[key] = card

    return list(best_by_word.values())


def load_history() -> list[dict[str, Any]]:
    try:
        value = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except Exception:
        return []


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


def parse_weekdays(raw: str) -> set[int]:
    """
    Python weekday numbers:
    Monday=0, Tuesday=1, Wednesday=2, Thursday=3,
    Friday=4, Saturday=5, Sunday=6.
    """
    result: set[int] = set()

    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue

        day = int(item)
        if day < 0 or day > 6:
            raise ValueError("HARD_DAYS must contain only numbers from 0 to 6.")

        result.add(day)

    return result


def eligible_content(card: dict[str, Any]) -> bool:
    # The lock screen needs all three pieces.
    return bool(
        card["word"]
        and card["definition"]
        and card["example"]
    )


def select_word(
    cards: list[dict[str, Any]],
    history: list[dict[str, Any]],
    date: dt.date,
) -> tuple[dict[str, Any], str, bool]:
    known_min = env_int("KNOWN_MIN", 5)
    hard_min = env_int("HARD_MIN", 26)      # knownCount > 25
    repeat_window = env_int("REPEAT_WINDOW", 120)
    hard_days = parse_weekdays(
        os.environ.get("HARD_DAYS", "2,6")  # Wednesday + Sunday
    )

    is_hard_day = date.weekday() in hard_days

    if is_hard_day:
        mode = "hard"
        candidates = [
            card
            for card in cards
            if eligible_content(card)
            and card["known_count"] >= hard_min
        ]

        # Never break the feed if the hard pool is empty.
        if not candidates:
            mode = "hard-fallback"
            candidates = [
                card
                for card in cards
                if eligible_content(card)
                and card["known_count"] >= known_min
            ]

    else:
        mode = "regular"
        candidates = [
            card
            for card in cards
            if eligible_content(card)
            and known_min <= card["known_count"] < hard_min
        ]

        # If no regular cards remain, use the broader known pool.
        if not candidates:
            mode = "regular-fallback"
            candidates = [
                card
                for card in cards
                if eligible_content(card)
                and card["known_count"] >= known_min
            ]

    if not candidates:
        raise RuntimeError(
            "No eligible cards have word + English definition + example "
            "under the configured knownCount thresholds."
        )

    recent_words = {
        str(item.get("word", "")).casefold()
        for item in history[-repeat_window:]
        if item.get("word")
    }

    fresh = [
        card
        for card in candidates
        if card["word"].casefold() not in recent_words
    ]

    # If the pool is smaller than the no-repeat window, restart gracefully.
    pool = fresh or candidates

    # Stable random choice for each calendar day.
    seed = (
        f"{date.isoformat()}|{mode}|{len(pool)}|"
        + "|".join(
            sorted((card["id"] or card["word"]) for card in pool)
        )
    )

    randomizer = random.Random(seed)
    chosen = randomizer.choice(pool)

    return chosen, mode, is_hard_day


def single_line(value: str) -> str:
    """
    TXT output is only a fallback. JSON is preferred in KWGT.
    Avoid ~ because it is the TXT delimiter.
    """
    return re.sub(
        r"\s+",
        " ",
        str(value).replace("~", "—"),
    ).strip()


def main() -> int:
    deck_ids = split_deck_ids(
        os.environ.get("DUOCARDS_DECK_IDS", "")
    )

    if not deck_ids:
        print(
            "Missing GitHub secret DUOCARDS_DECK_IDS.",
            file=sys.stderr,
        )
        return 2

    print(
        f"Using {len(deck_ids)} unique Deck ID(s). "
        "Duplicate IDs are ignored automatically."
    )

    # Workflow runs at 04:17 UTC, safely after midnight in São Paulo.
    now_sao_paulo = (
        dt.datetime.now(dt.timezone.utc)
        - dt.timedelta(hours=3)
    )
    today = now_sao_paulo.date()

    force_new = (
        os.environ.get("FORCE_NEW", "").lower()
        in {"1", "true", "yes"}
    )

    # Rerunning the workflow on the same day won't change the word.
    if WORD_JSON.exists() and not force_new:
        try:
            current = json.loads(
                WORD_JSON.read_text(encoding="utf-8")
            )
            if current.get("date") == today.isoformat():
                print("Today's word already exists. Nothing to change.")
                return 0
        except Exception:
            pass

    cards = fetch_all_decks(deck_ids)
    history = load_history()

    chosen, mode, is_hard_day = select_word(
        cards,
        history,
        today,
    )

    hard_day_label = "hard day" if is_hard_day else ""

    output = {
        "word": chosen["word"],
        "definition": chosen["definition"],
        "example": chosen["example"],
        "translation": chosen["translation"],
        "known_count": chosen["known_count"],
        "hard_day": is_hard_day,
        "hard_day_label": hard_day_label,
        "mode": mode,
        "date": today.isoformat(),
        "source": "DuoCards",
    }

    WORD_JSON.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # Fallback output:
    # word~definition~example~hard day
    WORD_TXT.write_text(
        "~".join(
            [
                single_line(output["word"]),
                single_line(output["definition"]),
                single_line(output["example"]),
                single_line(output["hard_day_label"]),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    history.append(
        {
            "date": today.isoformat(),
            "word": chosen["word"],
            "known_count": chosen["known_count"],
            "mode": mode,
            "hard_day": is_hard_day,
        }
    )

    repeat_window = env_int("REPEAT_WINDOW", 120)
    history_to_keep = max(repeat_window * 2, 300)

    HISTORY_FILE.write_text(
        json.dumps(
            history[-history_to_keep:],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    hard_min = env_int("HARD_MIN", 26)
    known_min = env_int("KNOWN_MIN", 5)

    regular_pool = sum(
        1
        for card in cards
        if eligible_content(card)
        and known_min <= card["known_count"] < hard_min
    )

    hard_pool = sum(
        1
        for card in cards
        if eligible_content(card)
        and card["known_count"] >= hard_min
    )

    print(
        f"Published {chosen['word']!r}. "
        f"mode={mode}; "
        f"knownCount={chosen['known_count']}; "
        f"hard_day={is_hard_day}; "
        f"unique_cards={len(cards)}; "
        f"regular_pool={regular_pool}; "
        f"hard_pool={hard_pool}."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
