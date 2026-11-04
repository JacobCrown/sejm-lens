import copy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from apps.assets.schema import DeclarationData, Money, parse_decimal
from apps.assets.verification import ERROR, WARNING, amount_forms, normalize, number_forms, verify

from .synthetic import OCR_TEXT, bad_payload, good_payload


def codes(report, level=None):
    return {i.code for i in report.issues if level is None or i.level == level}


def run(payload, **kwargs):
    kwargs.setdefault("ocr_confidence", 0.97)
    return verify(DeclarationData.model_validate(payload), OCR_TEXT, **kwargs)


# ---------- schema ----------

@pytest.mark.parametrize(
    "raw, expected",
    [
        (1207, "1207"), (62691.41, "62691.41"), ("1 207,50", "1207.50"), ("499.000", "499000"),
        ("150.000,00", "150000.00"), ("107728PLN", "107728"), ("1,5", "1.5"), ("2 628 000", "2628000"),
        ("45.5", "45.5"), ("1,234.56", "1234.56"),
    ],
)
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["brak", "", True, None, [1]])
def test_parse_decimal_rejects(raw):
    with pytest.raises(ValueError):
        parse_decimal(raw)


def test_currency_aliases_and_errors():
    assert Money(amount="1", currency="zł").currency == "PLN"
    assert Money(amount="1", currency="EURO").currency == "EUR"
    assert Money(amount="1", currency=None).currency == "PLN"
    with pytest.raises(ValidationError):
        Money(amount="1", currency="euros-ish")


def test_good_payload_is_valid_and_coerces():
    data = DeclarationData.model_validate(good_payload())
    assert data.declarant.birth_date.isoformat() == "1970-02-01"
    assert data.meta.declaration_date.isoformat() == "2024-07-15"
    assert data.real_estate[1].area_m2 == Decimal("54.8")
    assert data.meta.year == 2024


def test_schema_rejects_bad_enum_and_missing_quote():
    p = good_payload()
    p["real_estate"][0]["kind"] = "CASTLE"
    with pytest.raises(ValidationError) as exc:
        DeclarationData.model_validate(p)
    assert "real_estate.0.kind" in str(exc.value)

    p = good_payload()
    del p["holdings"][0]["quote"]
    with pytest.raises(ValidationError):
        DeclarationData.model_validate(p)


def test_extra_keys_are_ignored_and_year_parsed_from_text():
    p = good_payload()
    p["unexpected"] = 1
    p["meta"]["year"] = "stan na początek kadencji 2024"
    assert DeclarationData.model_validate(p).meta.year == 2024


# ---------- weryfikacja ----------

def test_number_forms_handle_polish_formats():
    forms = number_forms("570 000,00 PLN; 499.000 zł; 62691,41 PLN; 107728PLN; 5954CHF; 1,4946 ha")
    for expected in ["570000", "499000", "62691", "6269141", "107728", "5954", "14946"]:
        assert expected in forms
    assert "907" not in forms


def test_number_forms_understand_millions_and_thousands():
    forms = number_forms("o wartości: OK 1,2 MLN PLN; udziały 45 tys. zł; 3 mln zł; 2,5 tysiąca")
    assert {"1200000", "45000", "3000000", "2500"} <= forms
    assert "12" not in number_forms("tys")  # the word alone, with no number, adds nothing


def test_amount_given_in_millions_is_grounded():
    ocr = "x " * 60 + "1. Dom o powierzchni: OK 265 m², o wartości: OK 1,2 MLN PLN, tytuł prawny: współwłasność"
    data = DeclarationData.model_validate({
        "declarant": {"full_name": "Jan Testowy"},
        "real_estate": [{"kind": "HOUSE", "area_m2": 265, "value": {"amount": 1200000, "approximate": True},
                         "quote": "Dom o powierzchni: OK 265 m², o wartości: OK 1,2 MLN PLN"}],
    })
    assert not verify(data, ocr).errors


def test_amount_forms():
    assert amount_forms(Decimal("570000.00")) == {"570000"}
    assert {"6269141", "62691"} & amount_forms(Decimal("62691.41"))


def test_normalize():
    assert normalize("  Wartość: ŁÓDŹ, 5\u00a0000 zł ") == "wartosc lodz 5 000 zl"


def test_good_answer_is_fully_grounded():
    report = run(good_payload(), expected_name="Jan Testowy", expected_year=2024)
    assert not report.errors, report.messages()
    assert report.grounded_ratio == 1.0
    assert report.confidence > 0.9


def test_digit_swap_is_caught():
    p = good_payload()
    p["savings_pln"][0]["money"]["amount"] = 107782
    report = run(p)
    assert "ungrounded_amount" in codes(report, ERROR)
    assert report.fixable_errors


def test_sum_of_items_is_caught():
    report = run(bad_payload())
    assert {"ungrounded_amount", "currency_mismatch", "quote_not_found", "ungrounded_quantity"} <= codes(report, ERROR)


def test_amount_borrowed_from_another_line_is_flagged():
    p = good_payload()
    p["holdings"][0]["value"] = {"amount": 550000, "currency": "PLN"}
    report = run(p)
    assert "amount_outside_quote" in codes(report, WARNING)
    assert not report.errors


def test_fabricated_quote_is_error_but_ocr_noise_is_tolerated():
    p = good_payload()
    p["holdings"][0]["quote"] = "20000 ORLEN akcje nabyte w drodze spadku"
    assert "quote_not_found" in codes(run(p), ERROR)

    p = good_payload()
    p["savings_pln"][0]["quote"] = "środki pienieżne zgromadzone w walucie polskiej: 107728 PLN"
    assert not run(p).errors


def test_currency_must_match_quote():
    p = good_payload()
    p["savings_foreign"][0]["money"]["currency"] = "GBP"
    assert "currency_mismatch" in codes(run(p), ERROR)


def test_wrong_declarant_is_not_fixable():
    report = run(good_payload(), expected_name="Piotr Kowalski")
    assert "wrong_declarant" in codes(report, ERROR)
    assert not report.fixable_errors
    assert not run(good_payload(), expected_name="Jan Testowy").errors


def test_name_matching_ignores_diacritics_and_extra_surnames():
    p = good_payload()
    p["declarant"]["full_name"] = "JAN TESTOWY; KOWALSKI"
    assert not run(p, expected_name="Jan Testowy").errors
    p["declarant"]["full_name"] = "ŁUKASZ ŻÓŁTY"
    report = run(p, expected_name="Lukasz Zolty")
    assert "wrong_declarant" not in codes(report)
    assert "declarant_not_in_ocr" in codes(report, WARNING)


def test_implausible_values():
    p = good_payload()
    p["real_estate"][0]["area_m2"] = -3
    p["liabilities"][0]["amount"]["amount"] = 5_000_000_000
    report = run(p)
    assert {"implausible_area", "implausible_amount"} <= codes(report, ERROR)


def test_bad_share_and_duplicates_warn():
    p = good_payload()
    p["real_estate"][0]["share"] = "połowa"
    p["holdings"].append(copy.deepcopy(p["holdings"][0]))
    assert {"bad_share", "duplicate_entry"} <= codes(run(p), WARNING)


def test_empty_ocr_blocks_everything():
    data = DeclarationData.model_validate(good_payload())
    report = verify(data, "=== STRONA 1 ===\nxx")
    assert "ocr_empty" in codes(report, ERROR)
    assert report.confidence == 0.0


def test_low_ocr_confidence_lowers_score():
    high = run(good_payload(), ocr_confidence=0.98)
    low = run(good_payload(), ocr_confidence=0.6)
    assert "ocr_low_confidence" in codes(low, WARNING)
    assert low.confidence < high.confidence


def test_confidence_orders_good_above_bad():
    assert run(good_payload()).confidence > run(bad_payload()).confidence + 0.3


def test_report_serialises():
    d = run(bad_payload()).to_dict()
    assert d["checks"] > 10 and d["issues"] and 0 <= d["confidence"] <= 1
