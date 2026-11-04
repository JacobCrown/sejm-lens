"""A synthetic declaration (a made-up person) with the same structure and traps as real scans."""

import copy

OCR_TEXT = """=== STRONA 1 ===
Dziennik Ustaw Nr 104  - 5172 -  Poz. 708
Stan na początek kadencji 2024
Oświadczenie o stanie majątkowym
Ja, niżej podpisany(a),  JAN TESTOWY
urodzony(a)  01.02.1970  w  Poznaniu
POSEŁ NA SEJM RZECZYPOSPOLITEJ POLSKIEJ; DYREKTOR SZKOŁY
oświadczam, że posiadam wchodzące w skład małżeńskiej wspólności majątkowej lub stanowiące mój majątek odrębny:
I.
Zasoby pieniężne:
- środki pieniężne zgromadzone w walucie polskiej: 107728PLN
- środki pieniężne zgromadzone w walucie obcej:  5954CHF; 2815USD; 195141EURO
- papiery wartościowe: 1) IKE; 2) 1KZE
na kwotę:  1) 62691,41 PLN;  2) 56480,68 PLN
II.
1. Dom o powierzchni: 1/2 udziału w domu ok.115m2
o wartości:  550000
tytuł prawny:  współwłasność
2. Mieszkanie o powierzchni:  54,8 m2,
o wartości: 499.000 zł
tytuł prawny: własność
3. Gospodarstwo rolne: NIE DOTYCZY
4. Inne nieruchomości:
Działka niezabudowana, powierzchnia 1,4946 ha, o wartości: 45.000,00 zł, własność
V.
1. Posiadam akcje w spółkach z udziałem państwowych osób prawnych:
15400 PZU; 934 TAURON
2. Posiadam akcje w innych spółkach handlowych:
NIE DOTYCZY
VII.
1. Prowadzę działalność gospodarczą: NIE DOTYCZY
IX.
Inne dochody:
Uposażenie poselskie: 95.854,19 PLN brutto
Wynagrodzenie mieś. 8089Euro
X.
Samochód marki Skoda Octavia; rok 2019, wartość ok. 51.000 zł
XI.
Kredyt hipoteczny w CHF; BNP Paribas, pozostała kwota kapitału do spłaty 47145,19 CHF
KARTA KREDYTOWA KBC BANK - LIMIT 5000 EURO
Powyższe oświadczenie składam świadomy(a), iż na podstawie art. 233 § 1 Kodeksu karnego
15.07.2024
"""


def _m(amount, cur="PLN", approx=False):
    return {"amount": amount, "currency": cur, "approximate": approx}


def good_payload() -> dict:
    return {
        "declarant": {"full_name": "JAN TESTOWY", "birth_date": "01.02.1970", "birth_place": "Poznań",
                      "occupation": "poseł; dyrektor szkoły"},
        "meta": {"declaration_date": "15.07.2024", "as_of": "początek kadencji 2024", "year": 2024},
        "savings_pln": [{"label": "Środki w złotych", "money": _m(107728),
                         "quote": "środki pieniężne zgromadzone w walucie polskiej: 107728PLN"}],
        "savings_foreign": [
            {"label": "CHF", "money": _m(5954, "CHF"), "quote": "5954CHF"},
            {"label": "USD", "money": _m(2815, "USD"), "quote": "2815USD"},
            {"label": "EUR", "money": _m(195141, "EUR"), "quote": "195141EURO"},
        ],
        "securities": [
            {"label": "IKE", "money": _m(62691.41), "quote": "1) 62691,41 PLN"},
            {"label": "IKZE", "money": _m(56480.68), "quote": "2) 56480,68 PLN"},
        ],
        "real_estate": [
            {"kind": "HOUSE", "description": "dom", "area_m2": 115, "share": "1/2", "value": _m(550000),
             "legal_title": "współwłasność", "quote": "1/2 udziału w domu ok.115m2 o wartości: 550000"},
            {"kind": "APARTMENT", "area_m2": "54,8", "value": _m(499000), "legal_title": "własność",
             "quote": "Mieszkanie o powierzchni:  54,8 m2, o wartości: 499.000 zł"},
            {"kind": "LAND", "description": "działka niezabudowana", "area_m2": 14946, "value": _m(45000),
             "legal_title": "własność", "quote": "Działka niezabudowana, powierzchnia 1,4946 ha, o wartości: 45.000,00 zł"},
        ],
        "holdings": [
            {"kind": "STATE_COMPANY_STOCKS", "issuer": "PZU", "quantity": 15400, "quote": "15400 PZU"},
            {"kind": "STATE_COMPANY_STOCKS", "issuer": "TAURON", "quantity": 934, "quote": "934 TAURON"},
        ],
        "other_income": [
            {"source": "Uposażenie poselskie", "money": _m(95854.19), "tax_note": "brutto",
             "quote": "Uposażenie poselskie: 95.854,19 PLN brutto"},
            {"source": "Wynagrodzenie miesięczne", "money": _m(8089, "EUR"), "quote": "Wynagrodzenie mieś. 8089Euro"},
        ],
        "movables": [
            {"description": "Samochód osobowy", "make_model": "Skoda Octavia", "year": 2019,
             "value": _m(51000, approx=True), "quote": "Samochód marki Skoda Octavia; rok 2019, wartość ok. 51.000 zł"},
        ],
        "liabilities": [
            {"kind": "MORTGAGE", "description": "Kredyt hipoteczny w CHF", "creditor": "BNP Paribas",
             "amount": _m(47145.19, "CHF"),
             "quote": "BNP Paribas, pozostała kwota kapitału do spłaty 47145,19 CHF"},
            {"kind": "CREDIT_CARD", "description": "Karta kredytowa KBC Bank", "creditor": "KBC Bank",
             "amount": _m(5000, "EUR"), "is_limit": True, "quote": "KARTA KREDYTOWA KBC BANK - LIMIT 5000 EURO"},
        ],
        "notes": "",
        "model_confidence": 0.9,
    }


def bad_payload() -> dict:
    """An answer with the mistakes models typically make injected into it."""
    p = copy.deepcopy(good_payload())
    p["real_estate"][1]["value"]["amount"] = 909000  # suma zamiast pozycji
    p["savings_pln"][0]["money"]["currency"] = "EUR"  # waluta niezgodna z cytatem
    p["holdings"].append({"kind": "OTHER_STOCKS", "issuer": "Orlen", "quantity": 777, "quote": "777 ORLEN akcje zwykłe"})
    return p
