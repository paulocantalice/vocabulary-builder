# Vocabulary builder

Daily English vocabulary for a KWGT lock-screen widget, sourced automatically from one or more DuoCards decks.

## What appears on the lock screen

Each day the project publishes:

- **word**
- **first English paragraph** from DuoCards `theoryEn`
- **example sentence** from DuoCards `hint`
- a subtle **hard day** label on Wednesdays and Sundays

## Schedule

### Regular days
Monday, Tuesday, Thursday, Friday and Saturday:

`knownCount 5–25`

### Hard days
Wednesday and Sunday:

`knownCount >= 26`

On hard days `word.json` contains:

```json
"hard_day": true,
"hard_day_label": "hard day"
```

On regular days:

```json
"hard_day": false,
"hard_day_label": ""
```

That means the KWGT text element can simply read `hard_day_label`: it will automatically be blank on regular days.

## Multiple DuoCards decks

Create one GitHub Actions repository secret named:

`DUOCARDS_DECK_IDS`

Put one Deck ID per line:

```text
DECK_ID_CURRENT
DECK_ID_OLD_1
DECK_ID_OLD_2
```

Commas and semicolons also work.

Duplicate Deck IDs are ignored automatically.

If the same vocabulary word exists in several decks, the script keeps a single copy, preferring:

1. a card with both definition and example;
2. the larger `knownCount`;
3. the richer definition/example text.

## GitHub setup

1. Create a public repository named `vocabulary-builder`.
2. Upload all files from this project to the repository root.
3. Go to:
   `Settings → Secrets and variables → Actions`.
4. Click `New repository secret`.
5. Name it:
   `DUOCARDS_DECK_IDS`.
6. Put your DuoCards Deck IDs in the value, one per line.
7. Go to:
   `Actions → Daily DuoCards word`.
8. Click:
   `Run workflow`.

After the first successful run, the workflow runs automatically every day at about 01:17 São Paulo time.

## KWGT

Replace `YOUR_GITHUB_USERNAME` in the formulas below.

### Word

```text
$wg("https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/vocabulary-builder/main/word.json?d="+df(yyyyMMddHH), json, ".word")$
```

### Definition

```text
$wg("https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/vocabulary-builder/main/word.json?d="+df(yyyyMMddHH), json, ".definition")$
```

### Example

```text
$wg("https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/vocabulary-builder/main/word.json?d="+df(yyyyMMddHH), json, ".example")$
```

### Hard-day label

```text
$wg("https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/vocabulary-builder/main/word.json?d="+df(yyyyMMddHH), json, ".hard_day_label")$
```

Suggested styling for the hard-day label:

- small type
- regular or medium weight
- low opacity
- place above the main word
- do not add a background

Because the field is empty on normal days, the label disappears automatically.

The hourly cache-buster (`yyyyMMddHH`) helps KWGT pick up the GitHub update after the scheduled 01:17 São Paulo run, even if it fetched the previous day's file shortly after midnight.

## Example `word.json`

```json
{
  "word": "perfunctory",
  "definition": "done routinely and with little interest or care",
  "example": "He gave the document only a perfunctory glance.",
  "translation": "...",
  "known_count": 31,
  "hard_day": true,
  "hard_day_label": "hard day",
  "mode": "hard",
  "date": "2026-08-30",
  "source": "DuoCards"
}
```

## Changing the rules later

Edit `.github/workflows/update-word.yml`.

Relevant settings:

- `KNOWN_MIN: "5"` — lower bound for regular vocabulary.
- `HARD_MIN: "26"` — hard words are `knownCount > 25`.
- `HARD_DAYS: "2,6"` — Wednesday and Sunday.
- `REPEAT_WINDOW: "120"` — tries not to repeat any of the last 120 words.

## Note about knownCount

The open-source Duoload implementation treats:

- `0` as `new`
- `1–4` as `learning`
- `>=5` as `known`

DuoCards does not publicly document that `knownCount` is literally the number of right-swipes, so Vocabulary builder treats it as a progress/review counter rather than assuming its exact semantics.

## Technical note

This is an unofficial integration using DuoCards' current GraphQL structure. DuoCards can change its API/schema in the future, in which case the script may need a small update.
