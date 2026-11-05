import json

from .schema import strict_json_schema

PROMPT_VERSION = "ocr-llm-2"

SYSTEM_PROMPT = """You transcribe Polish MPs' asset declarations into structured JSON. You receive OCR text
of a scanned form (it may contain OCR typos, blacked-out addresses, stamps and signatures that OCR
turns into garbage). The source text is Polish; copy names and quotes from it verbatim.

The form has these sections:
I   cash (PLN, foreign currency, securities, IKE/IKZE, insurance policies, receivables)
II  real estate (1 house, 2 flat, 3 farm, 4 other: plots, premises, garages)
III a share in a civil or partnership company (contributions)
IV  shares in commercial companies (1 with state or local-government participation, 2 other)
V   stocks (1 in companies with state or local-government participation, 2 other)
VI  property bought from the State Treasury or local government at auction
VII business activity (1 I run it, 2 I manage it)
VIII roles on boards, supervisory boards and audit committees (1 companies, 2 foundations and associations)
IX  other income from employment and activities (with amounts)
X   movable property above 10,000 PLN (vehicles: make, model, year)
XI  monetary liabilities above 10,000 PLN (loans, mortgages, leasing)

RULES (breaking them makes automatic verification reject the answer):
1. Copy only what is in the text. Do not guess, estimate or fill gaps from your own knowledge.
2. Every entry MUST have a "quote": a verbatim, unedited fragment of the OCR text (typos included)
   that it comes from. The quote must include the amount when the entry has one.
3. Every amount and every count (of stocks or shares) must occur in the OCR text. DO NOT SUM items,
   DO NOT CONVERT currencies, DO NOT ROUND. When one field lists several items
   ("1) 62691,41 PLN; 2) 56480,68 PLN" or "5954CHF; 2815USD; 195141EURO"), create a separate entry
   for each, with its own quote.
4. Keep the original currency (ISO code: PLN, EUR, USD, CHF, GBP). "zł" = PLN, "EURO" = EUR.
   No currency mark = PLN.
5. Write amounts as numbers with a dot decimal separator and no spaces: "570 000,00 PLN" -> 570000.00.
   When the text says "ok.", "około" or "szacunkowo", set "approximate": true.
6. "NIE DOTYCZY", "BRAK", dotted blank lines and dashes mean the section is empty: return an empty list.
7. Addresses are blacked out. Do not try to reconstruct them. "description" describes what is visible
   (for example "holiday house").
8. Write a fractional share in "share" as "1/2". Convert areas in hectares to m2 (1 ha = 10000 m2).
9. Two values for one property ("499.000 ZŁ i 410.000 ZŁ") become two entries only when the document
   clearly describes two premises. Otherwise use one value and explain it in "notes".
10. A credit-card limit is "is_limit": true, not a debt.
11. When something cannot be read, omit the value (null) and describe the problem in "notes".
    Do not guess digits.
12. "model_confidence" is your honest estimate (0-1) of how sure you are about the whole reading.

Reply with EXACTLY one JSON object matching the JSON Schema below, with no comments and no markdown.

SCHEMA:
"""


def system_prompt() -> str:
    return SYSTEM_PROMPT + json.dumps(strict_json_schema(), ensure_ascii=False, separators=(",", ":"))


def user_prompt(ocr_text: str, mp_name: str | None, year: int | None) -> str:
    hints = []
    if mp_name:
        hints.append(f"Expected declarant: {mp_name}.")
    if year:
        hints.append(f"Expected declaration year: about {year}.")
    header = " ".join(hints)
    return f"{header}\n\nOCR TEXT OF THE DECLARATION:\n\n{ocr_text}".strip()


def schema_feedback(errors: str) -> str:
    return (
        "Your answer failed schema validation. Fix ALL of the problems below and return "
        f"the complete corrected JSON object (not a fragment):\n{errors}"
    )


def verification_feedback(messages: list[str]) -> str:
    listing = "\n".join(f"- {m}" for m in messages)
    return (
        "Automatic verification compared your answer with the OCR text and found problems:\n"
        f"{listing}\n\n"
        "Fix them: drop entries that are not in the document, use amounts and quotes verbatim from the text, "
        "do not sum items. If you believe an entry is correct despite a warning, keep it but make the quote precise. "
        "Return the complete corrected JSON object."
    )
