# Sejm Watch

**See how Poland's parliament votes, and what its members declare they own, in plain language.**

Sejm Watch is an open-source web app that pulls public data about the Polish Sejm (the lower house of parliament) and turns it into something a person can actually read: how every MP voted, how disciplined each club is, and what each MP's asset declaration says, extracted from the official scanned PDFs and checked automatically.

![From scanned declaration to structured data](docs/media/pipeline.gif)

*The animation is generated from a real extraction stored in the database (`scripts/make_pipeline_gif.py`). The scan is blurred on purpose.*

![An MP profile with the asset summary](docs/media/profile.png)

## Features

- **Votings**: result of every vote, a hemicycle with each MP's vote, a per-club table (for, against, abstained, absent) and a list of MPs who voted against their own club.
- **MP profiles**: searchable by name, club and district, with attendance, loyalty to the club and full voting history.
- **Clubs**: voting cohesion and which clubs vote alike most often.
- **Asset declarations**: assets, income and liabilities converted to PLN, shown as tiles, a structure chart, a year-over-year chart and short plain-language insights.
- **Automatic download of declarations** from the Sejm website for every MP (`sync_declarations --fetch`).
- **OCR + LLM extraction with verification**: scans are read by the Datalab convert API, a model of your choice structures the stored text, and a deterministic checker compares every number with that text.
- **Cost transparency**: every extraction stores the model, tokens and the price of the model calls, and the MP profile shows how much it cost to compute the data.
- **Pluggable everywhere**: any OpenAI-compatible LLM API (OpenAI, OpenRouter, Groq, Together, Mistral, DeepSeek, or local Ollama, LM Studio, vLLM), and a simple protocol for writing your own extractor.

## Tech stack

| Layer | What is used |
| --- | --- |
| Backend | Python 3.12, Django 5.1/5.2, PostgreSQL 16 |
| Frontend | Django templates, HTMX, Tailwind CSS (CDN), Apache ECharts |
| Parliament data | Official [Sejm API](https://api.sejm.gov.pl), `httpx` |
| Declaration download | `curl_cffi` (Chrome TLS fingerprint, no browser needed) |
| OCR | [Datalab](https://documentation.datalab.to) convert API (`accurate`). Optional local preview: [Chandra](https://github.com/datalab-to/chandra) (`uv sync --extra ocr`) |
| Structuring | Any OpenAI-compatible `/chat/completions` API, `pydantic` schema |
| Currency | NBP exchange rates (with an offline fallback table) |
| Tooling | `uv`, `pytest` + `pytest-django`, Docker Compose |

## Data flow

```mermaid
flowchart LR
  SejmAPI[Sejm API] --> SyncSejm[sync_sejm]
  SejmSite[MP pages on sejm.gov.pl] --> SyncDecl[sync_declarations]
  Manual[PDF from file, URL or CSV] --> SyncDecl
  SyncSejm --> Raw[(RawDocument)]
  SyncDecl --> Raw
  Raw --> Domain["Club, MP, Sitting, Voting, Vote"]
  Raw --> Extract["DeclarationExtractor: mock or ocr_llm"]
  Extract --> Items["AssetItem and ExtractionRun"]
  Domain --> Agg["Club stats, attendance, loyalty"]
  Items --> Summary["Wealth summary and year-over-year changes"]
  Agg --> Web[Django views, HTMX, ECharts]
  Summary --> Web
```

1. **Raw layer** (`apps.core.RawDocument`): every API response and every PDF is stored with its SHA-256. If the hash did not change, processing is skipped, which makes every sync idempotent and the data reproducible.
2. **Domain layer** (`apps.parliament`): upserts by natural keys (term, sitting number, voting number, MP id). A vote stores the MP's club *at the time of the vote*, so later club changes do not rewrite history.
3. **Aggregates** (`apps/parliament/services/stats.py`) are computed in SQL, with no LLM involved and no cost:
   - `ClubVotingStats`: share of votes per club, club majority and cohesion (Hix-Noury-Roland agreement index, 1 means everyone voted the same);
   - MP attendance: share of votings the MP was present for;
   - loyalty: share of votes matching the club majority (independents and votings without a clear club majority excluded).
4. **Declarations** (`apps.assets`): a PDF goes to an extractor, the result is validated, currencies are converted, and the summary is computed from the stored items. See the next section.

## How an MP's financial summary is built

The official declarations are **scans with no text layer**, often handwritten or typed, with addresses blacked out. The pipeline has two stages and a strict division of labour: the model *transcribes*, code *checks and calculates*.

```mermaid
flowchart LR
  PDF --> Datalab["Datalab convert API"]
  Datalab --> Stored["ocr_text stored on the declaration"]
  Stored --> LLM["LLM: JSON following a schema"]
  LLM --> Schema{"Schema validation"}
  Schema -->|errors| LLM
  Schema -->|ok| Verify{"Check against the OCR text"}
  Verify -->|"fixable errors"| LLM
  Verify --> Items["Items, confidence, status"]
  Items --> Totals["Currency conversion and totals in code"]
```

**1. OCR, then the model.** Scans are read by the [Datalab convert API](https://documentation.datalab.to/docs/recipes/conversion/conversion-api-overview) (`mode=accurate`, exact transcription, markdown). `manage.py ocr_declarations` stores that markdown on the declaration row for that MP, year and kind (`ocr_text`, plus the cost and the response in `ocr_response`). A row that already has text is skipped, so the paid call is not repeated. `manage.py extract_declarations` only sends the stored text to your LLM. It does not call Datalab.

The picture is one page of a public filing (Andrzej Adamczyk). Left: the scan and the regions that were read. Right: the text kept for the model.

![A public declaration page and the text read from it](docs/media/chandra-preview.png)

`uv run python scripts/preview_ocr.py file.pdf --out /tmp/ocr-preview` draws the same kind of page locally with Chandra. It does not call Datalab and does not write to the database. Chandra needs `uv sync --extra ocr`.

**2. Transcription by the model (`apps/assets/extractors/ocr_llm.py`).** The model receives the OCR text, a prompt describing the sections I-XI of the official form and a JSON Schema (`apps/assets/schema.py`). Its rules: copy only what is in the text, do not sum, do not convert currencies, do not round, do not guess digits, and attach a verbatim `quote` from the OCR text to every entry. It returns one JSON object. The full answer is kept in `AssetDeclaration.structured`.

**3. Verification (`apps/assets/verification.py`).** Schema validation only checks the shape, so every answer also goes through a deterministic check against the OCR text, with no LLM:

| Check | Level | Catches |
| --- | --- | --- |
| amount appears in the quote, or at least in the document | error / warning | invented amounts, **totals instead of line items**, swapped digits, a number from another line |
| number of shares and stocks appears in the document | error | invented quantities |
| quote appears in the OCR text (trigram similarity, tolerant to OCR typos) | error / warning | invented entries |
| currency matches the one in the quote (`zł`, `EURO`, `5954CHF`) | error | mixed-up currencies |
| declarant is the right MP | error | **the wrong file** |
| ranges: negative or unrealistic amounts, areas, shares as fractions, duplicates, year, date | error / warning | logic errors |
| OCR confidence | warning | poor scans |

Polish number formats are understood (`570 000,00`, `499.000`, `107728PLN`, `1,2 MLN`).

**Repair loop.** Schema errors and fixable verification errors are sent back to the model as a concrete list of problems, up to `LLM_MAX_REPAIRS` times, and the best answer is kept. If a later call comes back empty, that earlier answer is still kept. Errors the model cannot fix (for example a wrong file, or a year printed on the scan that is not the filing year) are not retried.

**4. Calculation in code (`apps/assets/services/`).**
- Foreign-currency items are converted to PLN with the NBP rate from 31 December of the declaration year (this is the date the declaration describes). An offline table is used, and flagged, when NBP is unreachable.
- Items are grouped into savings (PLN, foreign currency, securities), real estate and other assets (shares, vehicles, insurance, receivables). **Net worth = assets - liabilities**; income is shown separately.
- For each year the profile uses one declaration (end of term, then annual, then start of term). Annual declarations are filed by 30 April and describe the state on 31 December of the previous year, so a declaration filed in 2025 is stored as year 2024.
- The year-over-year chart and the insights ("net worth grew by ...", "liabilities fell by ...") are produced by deterministic code, not by a model.

**5. Confidence and review.** Overall confidence is 50% agreement with the OCR text, 30% OCR confidence and 20% the model's own estimate, minus penalties for errors and warnings. A declaration gets the status `NEEDS_REVIEW` when any verification error occurred or confidence is below `ASSET_REVIEW_THRESHOLD`. The profile then shows a "problems with the reading" panel, and the entry can be corrected in the Django admin. Every run is stored in `ExtractionRun`: extractor, model, prompt version, raw JSON, confidence, duration, **tokens and cost**.

### What it costs

Datalab bills each OCR call. That amount is stored on the declaration as `ocr_cost_usd`. The model call is a separate cost, recorded on every extraction run. With OpenRouter the price is read straight from the response (`usage.cost`); other providers return tokens only, which are stored and shown, and the amount is left empty rather than guessed. On an MP profile, under "Asset declarations", you see the model cost and, when Datalab reported it, the OCR cost. The admin shows every run.

Measured with `deepseek/deepseek-v4.1-flash-1017` on real scans: about **0.004 USD** for a short declaration, **0.007 USD** for a typical annual one, and **0.014 USD** for a long one with many plots of land that needed two repair rounds (3 model calls). Prices depend on the model and the provider.

### What the verification cannot catch

- A correct number attached to the wrong item *within the same quote* (for example an instalment instead of the value of a flat).
- The case where OCR and the model both read the same wrong character.
- **Partial ownership.** A line such as "3/8 of 660,000 PLN" is stored as value 660,000 with the share 3/8, and the total uses the written value, not 3/8 of it. See the roadmap.

Because of this, data produced automatically is always labelled as machine-read and linked to the source PDF.

### Pluggable extractor

The contract is in `apps/assets/extractors/base.py`:

```python
class DeclarationExtractor(Protocol):
    name: str
    def extract(self, pdf_bytes: bytes | None, context: DeclarationContext) -> ExtractedDeclaration: ...
```

- `mock` (default) does not read the PDF. It generates deterministic demo data that the UI labels as "demo data".
- `ocr_llm` is the pipeline described above.
- Register your own in `get_extractor` and select it with `ASSET_EXTRACTOR`.

## Quick start

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), PostgreSQL 14+ (or Docker).

```bash
cp .env.example .env
docker compose up -d db            # or any Postgres matching DATABASE_URL
uv sync
uv run python manage.py migrate
uv run python manage.py sync_sejm --sittings 5     # clubs, MPs, votings from the last 5 sittings
uv run python manage.py seed_demo_declarations     # demo declarations (clearly labelled)
uv run python manage.py runserver 0.0.0.0:8642
```

Open <http://localhost:8642>. The admin is at `/admin/` (create an account with `uv run python manage.py createsuperuser`). Everything in Docker: `docker compose up --build`, then run commands with `docker compose exec web python manage.py ...`.

### Real declarations

```bash
# in .env: DATALAB_API_KEY, ASSET_EXTRACTOR=ocr_llm and the LLM_* settings below
uv run python manage.py check_llm                            # one tiny request to verify the LLM
uv run python manage.py sync_sejm --skip-votings             # MPs first
uv run python manage.py sync_declarations --fetch --limit 10 --year 2025
# everyone, newest year only: --fetch --latest
uv run python manage.py ocr_declarations --limit 10 --year 2025
# several years: --year 2025 2026
uv run python manage.py extract_declarations --limit 5 --year 2025
# several years: --year 2025 2026
```

`ocr_declarations` skips a declaration that already has OCR text. `--force` pays for a new read. The Docker image does not call Datalab. Run these commands on the host. `docker compose up` always points the web container at the Postgres service in Compose, even if `.env` contains another `DATABASE_URL`. Commands you run with `uv run python manage.py ...` use `DATABASE_URL` from `.env`, so a Neon connection string there writes straight into that database.

## Commands

| Command | What it does |
| --- | --- |
| `sync_sejm [--sittings N \| --sitting X \| --all] [--skip-votings] [--force]` | Fetches the term, clubs, MPs, sittings and votings, then recomputes aggregates. Unchanged votings are skipped. |
| `recompute_stats` | Recomputes club results, attendance and loyalty without fetching. |
| `sync_declarations --fetch [--mp ID] [--year Y] [--latest] [--limit N] [--force] [--extract]` | Downloads declarations. `--year` keeps one year. `--latest` keeps the newest year on each MP's page (both kinds, when that year has two). `--limit` is the first N MPs by id. A row whose PDF is gone from disk is downloaded again. |
| `sync_declarations --mp ID --year Y (--file PDF \| --url URL) [--extract]` | Imports one PDF and optionally analyses it right away. |
| `sync_declarations --manifest file.csv [--extract]` | Imports many at once. The CSV has the columns `mp_id,year,kind,url,file`. |
| `ocr_declarations [--mp ID] [--year Y ...] [--limit N] [--force]` | Sends stored PDFs to Datalab. `--limit` is how many MPs (lowest id first). `--year 2025` or `--year 2025 2026` keeps only those years. Skips text already stored, and skips a row whose PDF is missing from disk. |
| `clear_ocr [--mp ID] [--year Y ...] [--limit N] [--yes]` | Deletes stored OCR text. The declaration row, the PDF and the LLM result stay. Without `--yes` it only prints the count. |
| `clear_declarations [--mp ID] [--year Y ...] [--limit N] [--missing] [--yes]` | Deletes real declaration rows and the PDF. Demo rows stay. `--missing` deletes only rows whose file is gone. Without `--yes` it only prints the count. |
| `clear_extractions [--mp ID] [--year Y ...] [--limit N] [--yes]` | Deletes a real LLM result and sets that declaration back to pending. Demo rows (`is_demo`) and stored OCR text stay. |
| `extract_declarations [--extractor mock\|ocr_llm] [--mp ID] [--year Y ...] [--limit N] [--all] [--review] [--retry-failed] [--reocr]` | Sends the stored OCR text to the LLM. `--limit` is how many MPs. `--year` works like `ocr_declarations`. It does not call Datalab. |
| `analyze_pdf file.pdf [--name "First Last"] [--year Y] [--json] [--ocr-only] [--ocr-text file.txt] [--save-ocr file.txt]` | Processes any PDF without touching the database. The quickest way to try your own model. |
| `check_llm` | Verifies the LLM provider from `.env` with one short request. |
| `seed_demo_declarations [--years 2023 2024 2025] [--limit N]` | Generates demo data for MPs without real declarations. |
| `fetch_mp_photos [--term N] [--workers N] [--force]` | Downloads MP portraits into `static/portraits/`. Files already there are skipped. Commit that directory so production does not hotlink `api.sejm.gov.pl`. |

A full term sync (`--all`) is more than 5,000 votings with 460 votes each. It takes a few minutes, and later runs only fetch changes. Run `sync_sejm` from cron in production, for example hourly on sitting days.

### Where the PDFs come from

The official API has no declarations. The list is on each MP's page on sejm.gov.pl (the "Asset declarations" section, loaded through XPages) and the scans live in ORKA (`orka.sejm.gov.pl`). Both reject plain `httpx`, `curl` and `urllib` (HTTP 403 or an anti-bot challenge) because they check the client's TLS fingerprint. `sync_declarations --fetch` therefore uses `curl_cffi` impersonating Chrome. It does not start a browser or run JavaScript.

| Row on the MP page | Kind | Year |
| --- | --- | --- |
| Beginning of term | `START` | year of receipt |
| 1st, 2nd, 3rd... year of term | `ANNUAL` | year of receipt minus 1 |
| End of term | `END` | year of receipt |

A row is skipped with a warning (and counted in `skipped_rows`) when it has no scan link, an invalid date, a link outside sejm.gov.pl, a file that belongs to another MP, or an unknown label. When there are two files for the same year and kind, the later one wins. The scan URL contains a unique document id, so later runs do not download files that are already stored, and a corrected scan is downloaded again. Requests are spaced by `DECLARATION_FETCH_DELAY` and an error for one MP does not stop the rest. A full term is a few hundred pages and about two thousand PDFs, so the first run takes tens of minutes.

If the Sejm changes the page layout, the command reports that the MP page has no declarations section. Manual import (`--file`, `--url`, `--manifest`) keeps working. An anti-bot interstitial stops the command on the first MP instead of requesting every one.

## Configuring the LLM

Any API compatible with OpenAI's `POST /chat/completions` works, with no provider SDK. In `.env`:

```bash
ASSET_EXTRACTOR=ocr_llm
LLM_PROVIDER=openai          # openai | openrouter | groq | together | mistral | deepseek | ollama | lmstudio | vllm
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
```

For a custom provider or gateway set `LLM_BASE_URL=https://my-host/v1` (it takes precedence over `LLM_PROVIDER`). A local model through Ollama needs no key:

```bash
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:14b
LLM_EXTRA_BODY={"options": {"num_ctx": 16384}}   # the prompt with schema and text is about 8k tokens, the answer up to 6k
```

If the provider rejects a parameter (for example `temperature` for reasoning models, or `response_format`), the client retries without it and `check_llm` reports it. `LLM_RESPONSE_FORMAT=json_schema` asks the provider to emit only `DeclarationData` (`strict: true`). That fixes the shape of the JSON. It does not check that a quoted amount is really on the scan; `verification.py` still does that. Use a generous `LLM_TIMEOUT` (at least 180 s): a full answer for a long declaration does not fit in 30 s.

## Configuration reference (`.env`)

| Variable | Default | Description |
| --- | --- | --- |
| `DATABASE_URL` | `postgres://sejm:sejm@localhost:5432/sejmwatch` | PostgreSQL connection |
| `SEJM_TERM` | `10` | Parliamentary term |
| `SEJM_HTTP_WORKERS` | `8` | Parallel downloads of votings |
| `DECLARATION_FETCH_DELAY` | `0.5` | Pause (seconds) between requests to sejm.gov.pl and ORKA during `sync_declarations --fetch` |
| `ASSET_EXTRACTOR` | `mock` | `mock` or `ocr_llm` |
| `ASSET_REVIEW_THRESHOLD` | `0.7` | Below this confidence a declaration needs review |
| `OCR_ENGINE` / `DATALAB_API_KEY` / `DATALAB_MODE` | `datalab` / empty / `accurate` | `ocr_declarations` calls the Datalab convert API. `accurate` is for scans. The result is stored on the declaration. |
| `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | `openai` / empty / empty / `gpt-4o-mini` | OpenAI-compatible LLM provider |
| `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` / `LLM_RESPONSE_FORMAT` / `LLM_TIMEOUT` | `0` / empty / `json_schema` / `180` | `json_schema` sends `DeclarationData` with `strict: true`, so the provider can only return that shape. `json_object` only requires some JSON object. `none` sends no format. |
| `LLM_EXTRA_HEADERS` / `LLM_EXTRA_BODY` | `{}` | Extra headers and request fields (JSON) |
| `LLM_MAX_REPAIRS` | `2` | How many times the model may fix its answer after errors |

## Testing

```bash
uv run pytest
```

Tests use `httpx.MockTransport` instead of the real Sejm API and LLM provider, and a fake session instead of sejm.gov.pl, with fallback currency rates instead of NBP, so they need neither network nor keys. They only need the PostgreSQL from `DATABASE_URL` because pytest-django creates a test database. The test document (`tests/synthetic.py`) is synthetic but has the same structure and traps as real scans. A slow test that loads Chandra runs with `RUN_OCR_TESTS=1 uv run pytest tests/test_ocr_llm.py`.

To regenerate the animation at the top of this file: `uv run python scripts/make_pipeline_gif.py` (from the committed sample), or `--from-db --mp ID --year Y` to rebuild it from a stored real extraction.

## Project layout

```
config/               settings, URLs
apps/core/            RawDocument, IngestionRun, money formatting, template filters
apps/parliament/      Sejm models, API client, sync, aggregates, hemicycle, views
apps/assets/          declarations: models, schema.py (model answer), verification.py, llm.py,
                      ocr/, extractors, sources.py (download from the Sejm site), currency, summaries, commands
templates/            Django templates + Tailwind (CDN) + HTMX + ECharts
scripts/              make_pipeline_gif.py, preview_ocr.py, seed_neon.sh
docs/media/           README images
tests/                pytest-django
```

## Deploy

Render builds the `prod` branch (`render.yaml`: Docker, free web service in Frankfurt). A push to `prod` redeploys. `collectstatic` runs while the image is built, so portraits committed under `static/portraits/` are served by WhiteNoise. A missing file falls back to `api.sejm.gov.pl`, which is what makes the MP list slow.

```bash
uv run python manage.py fetch_mp_photos
git push origin prod
```

Leave `ASSET_EXTRACTOR=mock` on Render. Do not put `DATALAB_API_KEY` or `LLM_API_KEY` there. OCR and extraction stay on your machine. The service uses the Neon `DATABASE_URL` from the Render dashboard. `docker compose up` ignores `.env` for the database and always uses the Compose Postgres. `uv run python manage.py ...` uses `DATABASE_URL` from `.env`, so a Neon string there writes straight into production.

### Replacing the Neon database

`scripts/seed_neon.sh` copies the Compose database (`localhost:5432` / `sejmwatch`, the default in `.env`) into Neon and replaces what is there. Use Neon's direct connection string with `sslmode=require`, not the pooled one. Accounts, sessions and the admin log are left behind. PDF bytes stay in local `media/`; Neon gets the rows, the OCR text and the extraction. With `DEBUG=false` the profile links to the Sejm PDF.

Run it after local `migrate`, so the dump has the current schema. Render runs `migrate` again on boot.

```bash
NEON_DATABASE_URL='postgres://USER:PASS@HOST/neondb?sslmode=require' scripts/seed_neon.sh
DATABASE_URL='postgres://USER:PASS@HOST/neondb?sslmode=require' uv run python manage.py createsuperuser
```

The script asks you to type `yes` before it writes anything.

## Roadmap

Planned, not implemented yet:

- [ ] **MP social media analysis.** Detect which platforms an MP is active on, and analyse the comments under their posts: what share is positive and what share is negative.
- [ ] **Sitting summaries.** From the recordings and transcripts of the Sejm YouTube channel, produce a summary of what each MP said and the overall atmosphere of the sitting.
- [ ] **Partial ownership.** Count only the declared share of jointly owned property (for example 3/8 of a house) instead of the full written value, with the interpretation shown on the profile.
- [ ] **Review UI.** Fix entries flagged `NEEDS_REVIEW` in the app itself instead of the Django admin.

Ideas and pull requests are welcome.

## Contributing

1. Fork the repository and create a branch.
2. Run `uv sync` and `uv run pytest` before and after your change; new behaviour needs a test.
3. Keep the division of labour: models transcribe, deterministic code verifies and calculates.
4. Open a pull request describing what changed and why.

## Data and legal note

All data comes from public sources: the [Sejm API](https://api.sejm.gov.pl), the MP pages on sejm.gov.pl and the declarations published by the Chancellery of the Sejm (home addresses and property locations are excluded from publication by law). Declarations are read by machines and may contain mistakes; the legally binding content is always the source PDF, which every profile links to. This project is not affiliated with the Sejm or the Chancellery of the Sejm.

## License

[MIT](LICENSE)
