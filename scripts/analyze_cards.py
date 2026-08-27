"""Download a DuoCards deck and report its useful field distribution."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

try:
    from .duocards_client import (
        DEFAULT_DECK_ID,
        fetch_all_cards,
        fetch_source,
        iter_theory,
    )
except ImportError:  # Direct execution: python scripts/analyze_cards.py
    from duocards_client import DEFAULT_DECK_ID, fetch_all_cards, fetch_source, iter_theory


def _present(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def analyze(cards: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_fronts = [
        card.get("front", "").strip().casefold()
        for card in cards
        if _present(card.get("front"))
    ]
    counts = Counter(
        card.get("knownCount")
        for card in cards
        if isinstance(card.get("knownCount"), int)
        and not isinstance(card.get("knownCount"), bool)
    )
    theory_values = list(iter_theory(cards))
    with_hint = sum(_present(card.get("hint")) for card in cards)
    with_both = 0
    for card in cards:
        s_card = card.get("sCard")
        theory = s_card.get("theory") if isinstance(s_card, dict) else None
        theory_en = theory.get("theoryEn") if isinstance(theory, dict) else None
        with_both += int(_present(card.get("hint")) and _present(theory_en))

    return {
        "total_cards": len(cards),
        "unique_fronts": len(set(normalized_fronts)),
        "duplicate_front_rows": len(normalized_fronts) - len(set(normalized_fronts)),
        "with_hint": with_hint,
        "with_theory_en": len(theory_values),
        "with_hint_and_theory_en": with_both,
        "known_count_exact": {str(key): counts[key] for key in sorted(counts)},
        "known_count_requested_buckets": {
            "0": counts[0],
            "1-4": sum(counts[value] for value in range(1, 5)),
            "5-15": sum(count for value, count in counts.items() if 5 <= value <= 15),
            "16-25": sum(count for value, count in counts.items() if 16 <= value <= 25),
            "26-35": sum(count for value, count in counts.items() if 26 <= value <= 35),
            "36-50": sum(count for value, count in counts.items() if 36 <= value <= 50),
            ">50": sum(count for value, count in counts.items() if value > 50),
        },
        "source_kinds": dict(
            sorted(
                Counter(
                    (card.get("source") or {}).get("kind", "<missing>")
                    if isinstance(card.get("source"), dict)
                    else "<missing>"
                    for card in cards
                ).items()
            )
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deck-id", default=DEFAULT_DECK_ID)
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Optional private path for the full raw card response.",
    )
    parser.add_argument(
        "--source-snapshot",
        type=Path,
        help="Also fetch each unique source and save the raw source responses.",
    )
    parser.add_argument(
        "--back-lang",
        help="DuoCards native/back language code used with --source-snapshot.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = fetch_all_cards(args.deck_id)
    if args.snapshot:
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    report = analyze(result.cards)
    if args.source_snapshot:
        source_ids = sorted(
            {
                card["sourceId"]
                for card in result.cards
                if isinstance(card.get("sourceId"), str) and card["sourceId"]
            }
        )
        sources = [
            fetch_source(source_id, args.deck_id, back_lang=args.back_lang)
            for source_id in source_ids
        ]
        args.source_snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.source_snapshot.write_text(
            json.dumps(sources, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        shared_cards = [
            shared_card
            for source in sources
            for shared_card in (source.get("sCards") or [])
            if isinstance(shared_card, dict)
        ]
        report["source_details"] = {
            "unique_sources": len(sources),
            "shared_cards": len(shared_cards),
            "with_theory_en": sum(
                _present((card.get("theory") or {}).get("theoryEn"))
                for card in shared_cards
                if isinstance(card.get("theory"), dict)
            ),
            "with_hint": sum(_present(card.get("hint")) for card in shared_cards),
            "in_my_deck": sum(card.get("isInMyDeck") is True for card in shared_cards),
        }
    report["pages"] = result.pages
    report["release_id"] = result.release_id
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
