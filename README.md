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
- **OCR + LLM extraction with verification**: scans are read locally, a model of your choice structures them, and a deterministic checker compares every number with the OCR text.
- **Cost transparency**: every extraction stores the model, tokens and the price of the model calls, and the MP profile shows how much it cost to compute the data.
- **Pluggable everywhere**: any OpenAI-compatible LLM API (OpenAI, OpenRouter, Groq, Together, Mistral, DeepSeek, or local Ollama, LM Studio, vLLM), and a simple protocol for writing your own extractor.

## Tech stack

| Layer | What is used |
| --- | --- |
| Backend | Python 3.12, Django 5.1/5.2, PostgreSQL 16 |
| Frontend | Django templates, HTMX, Tailwind CSS (CDN), Apache ECharts |
| Parliament data | Official [Sejm API](https://api.sejm.gov.pl), `httpx` |
| Declaration download | `curl_cffi` (Chrome TLS fingerprint, no browser needed) |
| OCR | [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) (PP-OCR models, runs locally), `pypdfium2` for rendering PDFs |
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
  PDF --> Text{"Text layer?"}
  Text -->|yes| Plain[Text from PDF]
  Text -->|no| OCR["PaddleOCR (local)"]
  OCR --> Rows[Lines grouped into rows]
  Plain --> LLM
  Rows --> LLM["LLM: JSON following a schema"]
  LLM --> Schema{"Schema validation"}
  Schema -->|errors| LLM
  Schema -->|ok| Verify{"Check against the OCR text"}
  Verify -->|"fixable errors"| LLM
  Verify --> Items["Items, confidence, status"]
  Items --> Totals["Currency conversion and totals in code"]
```

**1. OCR (`apps/assets/ocr/`).** PaddleOCR runs on your machine, so the scan itself never leaves it. On CPU this takes around 20 seconds per page (a typical declaration has 5-6 pages). The text is stored in `AssetDeclaration.ocr_text`, so changing the model or the prompt does not require running OCR again. If the PDF has a text layer, OCR is skipped.

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

**Repair loop.** Schema errors and fixable verification errors are sent back to the model as a concrete list of problems, up to `LLM_MAX_REPAIRS` times, and the best answer is kept. Errors the model cannot fix (for example a wrong file) are not retried.

**4. Calculation in code (`apps/assets/services/`).**
- Foreign-currency items are converted to PLN with the NBP rate from 31 December of the declaration year (this is the date the declaration describes). An offline table is used, and flagged, when NBP is unreachable.
- Items are grouped into savings (PLN, foreign currency, securities), real estate and other assets (shares, vehicles, insurance, receivables). **Net worth = assets - liabilities**; income is shown separately.
- For each year the profile uses one declaration (end of term, then annual, then start of term). Annual declarations are filed by 30 April and describe the state on 31 December of the previous year, so a declaration filed in 2025 is stored as year 2024.
- The year-over-year chart and the insights ("net worth grew by ...", "liabilities fell by ...") are produced by deterministic code, not by a model.

**5. Confidence and review.** Overall confidence is 50% agreement with the OCR text, 30% OCR confidence and 20% the model's own estimate, minus penalties for errors and warnings. A declaration gets the status `NEEDS_REVIEW` when any verification error occurred or confidence is below `ASSET_REVIEW_THRESHOLD`. The profile then shows a "problems with the reading" panel, and the entry can be corrected in the Django admin. Every run is stored in `ExtractionRun`: extractor, model, prompt version, raw JSON, confidence, duration, **tokens and cost**.

### What it costs

OCR is local and free. Only the model call costs money, and the app records it for every run. With OpenRouter the price is read straight from the response (`usage.cost`); other providers return tokens only, which are stored and shown, and the amount is left empty rather than guessed. On an MP profile, under "Asset declarations", you see the total cost of computing the displayed data and the model that was used; the admin shows every run.

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
uv sync --extra ocr                                          # PaddleOCR and PaddlePaddle (CPU)
# in .env: ASSET_EXTRACTOR=ocr_llm and the LLM_* settings below
uv run python manage.py check_llm                            # one tiny request to verify the provider
uv run python manage.py sync_sejm --skip-votings             # MPs first
uv run python manage.py sync_declarations --fetch --limit 5  # download PDFs for the first 5 MPs
uv run python manage.py extract_declarations --mp 1          # OCR + LLM for one MP
```

The Docker image does not include PaddleOCR (the `--extra ocr` variant has not been tested in a container), so run extraction locally.

## Commands

| Command | What it does |
| --- | --- |
| `sync_sejm [--sittings N \| --sitting X \| --all] [--skip-votings] [--force]` | Fetches the term, clubs, MPs, sittings and votings, then recomputes aggregates. Unchanged votings are skipped. |
| `recompute_stats` | Recomputes club results, attendance and loyalty without fetching. |
| `sync_declarations --fetch [--mp ID] [--year Y] [--limit N] [--force] [--extract]` | Finds and downloads MPs' declarations from the Sejm website. Files already stored are skipped, `--force` downloads them again. |
| `sync_declarations --mp ID --year Y (--file PDF \| --url URL) [--extract]` | Imports one PDF and optionally analyses it right away. |
| `sync_declarations --manifest file.csv [--extract]` | Imports many at once. The CSV has the columns `mp_id,year,kind,url,file`. |
| `extract_declarations [--extractor mock\|ocr_llm] [--mp ID] [--all] [--review] [--retry-failed] [--reocr]` | Analyses pending declarations. `--review` retries those needing review, `--reocr` forces fresh OCR instead of the stored text. |
| `analyze_pdf file.pdf [--name "First Last"] [--year Y] [--json] [--ocr-only] [--ocr-text file.txt] [--save-ocr file.txt]` | Processes any PDF without touching the database. The quickest way to try your own model. |
| `check_llm` | Verifies the LLM provider from `.env` with one short request. |
| `seed_demo_declarations [--years 2023 2024 2025] [--limit N]` | Generates demo data for MPs without real declarations. |

A full term sync (`--all`) is more than 5,000 votings with 460 votes each. It takes a few minutes, and later runs only fetch changes. Run `sync_sejm` from cron in production, for example hourly on sitting days.

### Where the PDFs come from

The official API has no declarations. The list is on each MP's page on sejm.gov.pl (the "Asset declarations" section, loaded through XPages) and the scans live in ORKA (`orka.sejm.gov.pl`). Both reject plain `httpx`, `curl` and `urllib` (HTTP 403 or an anti-bot challenge) because they check the client's TLS fingerprint. `sync_declarations --fetch` therefore uses `curl_cffi` impersonating Chrome. It does not start a browser or run JavaScript.

| Row on the MP page | Kind | Year |
| --- | --- | --- |
| Beginning of term | `START` | year of receipt |
| 1st, 2nd, 3rd... year of term | `ANNUAL` | year of receipt minus 1 |
| End of term | `END` | year of receipt |

A row is skipped with a warning (and counted in `skipped_rows`) when it has no scan link, an invalid date, a link outside sejm.gov.pl, a file that belongs to another MP, or an unknown label. When there are two files for the same year and kind, the later one wins. The scan URL contains a unique document id, so later runs do not download files that are already stored, and a corrected scan is downloaded again. Requests are spaced by `DECLARATION_FETCH_DELAY` and an error for one MP does not stop the rest. A full term is a few hundred pages and about two thousand PDFs, so the first run takes tens of minutes.

If the Sejm changes the page layout, the command reports `brak sekcji oświadczeń` for the MP page; manual import (`--file`, `--url`, `--manifest`) keeps working.

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

If the provider rejects a parameter (for example `temperature` for reasoning models, or `response_format`), the client retries without it and `check_llm` reports it. Use a generous `LLM_TIMEOUT` (at least 180 s): a full answer for a long declaration does not fit in 30 s.

## Configuration reference (`.env`)

| Variable | Default | Description |
| --- | --- | --- |
| `DATABASE_URL` | `postgres://sejm:sejm@localhost:5432/sejmwatch` | PostgreSQL connection |
| `SEJM_TERM` | `10` | Parliamentary term |
| `SEJM_HTTP_WORKERS` | `8` | Parallel downloads of votings |
| `DECLARATION_FETCH_DELAY` | `0.5` | Pause (seconds) between requests to sejm.gov.pl and ORKA during `sync_declarations --fetch` |
| `ASSET_EXTRACTOR` | `mock` | `mock` or `ocr_llm` |
| `ASSET_REVIEW_THRESHOLD` | `0.7` | Below this confidence a declaration needs review |
| `OCR_ENGINE` / `OCR_LANG` / `OCR_DEVICE` / `OCR_RENDER_SCALE` | `paddle` / `pl` / `cpu` / `1.6` | PaddleOCR settings. A higher scale is more accurate but slower. Use `OCR_DEVICE=gpu` with `paddlepaddle-gpu` |
| `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | `openai` / empty / empty / `gpt-4o-mini` | OpenAI-compatible LLM provider |
| `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` / `LLM_RESPONSE_FORMAT` / `LLM_TIMEOUT` | `0` / empty / `json_object` / `180` | Request parameters (`json_object`, `json_schema` or `none` for providers without a JSON mode) |
| `LLM_EXTRA_HEADERS` / `LLM_EXTRA_BODY` | `{}` | Extra headers and request fields (JSON) |
| `LLM_MAX_REPAIRS` | `2` | How many times the model may fix its answer after errors |

## Testing

```bash
uv run pytest
```

Tests use `httpx.MockTransport` instead of the real Sejm API and LLM provider, and a fake session instead of sejm.gov.pl, with fallback currency rates instead of NBP, so they need neither network nor keys. They only need the PostgreSQL from `DATABASE_URL` because pytest-django creates a test database. The test document (`tests/synthetic.py`) is synthetic but has the same structure and traps as real scans. A slow test with real PaddleOCR runs with `RUN_OCR_TESTS=1 uv run pytest tests/test_ocr_llm.py`.

To regenerate the animation at the top of this file: `uv run python scripts/make_pipeline_gif.py` (from the committed sample), or `--from-db --mp ID --year Y` to rebuild it from a stored real extraction.

## Project layout

```
config/               settings, URLs
apps/core/            RawDocument, IngestionRun, money formatting, template filters
apps/parliament/      Sejm models, API client, sync, aggregates, hemicycle, views
apps/assets/          declarations: models, schema.py (model answer), verification.py, llm.py,
                      ocr/, extractors, sources.py (download from the Sejm site), currency, summaries, commands
templates/            Django templates + Tailwind (CDN) + HTMX + ECharts
scripts/              make_pipeline_gif.py
docs/media/           README images
tests/                pytest-django
```

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
