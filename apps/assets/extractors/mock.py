"""Deterministic demo extractor.

Does not read the PDF. Builds a plausible estate from the MP id and the year so that
successive years form a consistent history. Results are marked as demo.
"""

import hashlib
import random
from decimal import ROUND_HALF_UP, Decimal

from ..models import AssetCategory
from .base import DeclarationContext, ExtractedDeclaration, ExtractedItem

BASE_YEAR = 2023


def _rng(*parts) -> random.Random:
    seed = int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16], 16)
    return random.Random(seed)


def _money(value: float, step: int = 100) -> Decimal:
    return (Decimal(value) / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step


OWNERSHIP = ["wspólność majątkowa małżeńska", "własność", "współwłasność 1/2"]
CARS = [
    "Toyota Corolla", "Skoda Octavia", "Volkswagen Passat", "Kia Sportage",
    "Hyundai Tucson", "Audi A4", "Toyota RAV4", "Mazda CX-5",
]
BANKS = ["PKO BP", "Pekao SA", "mBank", "Santander Bank Polska", "ING Bank Śląski"]


class MockExtractor:
    name = "mock"
    prompt_version = "mock-1"

    def extract(self, pdf_bytes: bytes | None, context: DeclarationContext) -> ExtractedDeclaration:
        base = _rng("profile", context.mp_sejm_id)
        year_rng = _rng("year", context.mp_sejm_id, context.year)
        years = max(0, context.year - BASE_YEAR)
        city = context.district_name or "Warszawa"
        items: list[ExtractedItem] = []

        cash = base.uniform(15_000, 420_000)
        for i in range(years):
            cash *= 1 + _rng("cash", context.mp_sejm_id, i).uniform(-0.12, 0.3)
        items.append(ExtractedItem(
            category=AssetCategory.CASH_PLN, label="Oszczędności w złotych",
            description="Rachunki bankowe i lokaty", value=_money(cash),
        ))

        if base.random() < 0.45:
            eur = base.uniform(2_000, 45_000) * (1 + 0.05 * years)
            items.append(ExtractedItem(
                category=AssetCategory.CASH_FOREIGN, label="Oszczędności w euro",
                value=_money(eur), currency="EUR",
            ))
        if base.random() < 0.35:
            sec = base.uniform(10_000, 280_000) * (1 + year_rng.uniform(-0.05, 0.15)) * (1 + 0.04 * years)
            items.append(ExtractedItem(
                category=AssetCategory.SECURITIES, label="Obligacje skarbowe i fundusze inwestycyjne",
                value=_money(sec),
            ))

        appreciation = 1.06 ** years
        flat_area = base.randint(38, 110)
        items.append(ExtractedItem(
            category=AssetCategory.REAL_ESTATE, label="Mieszkanie",
            description=city, area_m2=Decimal(flat_area),
            value=_money(flat_area * base.uniform(6_000, 15_000) * appreciation, 1000),
            ownership=base.choice(OWNERSHIP),
        ))
        if base.random() < 0.5:
            area = base.randint(90, 240)
            items.append(ExtractedItem(
                category=AssetCategory.REAL_ESTATE, label="Dom",
                description=f"okolice miejscowości {city}", area_m2=Decimal(area),
                value=_money(area * base.uniform(3_500, 8_000) * appreciation, 1000),
                ownership=base.choice(OWNERSHIP),
            ))
        if base.random() < 0.3:
            area = base.randint(700, 3_000)
            items.append(ExtractedItem(
                category=AssetCategory.REAL_ESTATE, label="Działka budowlana",
                area_m2=Decimal(area), value=_money(area * base.uniform(90, 380) * appreciation, 1000),
                ownership="własność",
            ))

        if base.random() < 0.15:
            items.append(ExtractedItem(
                category=AssetCategory.SHARES, label="Udziały w spółce z o.o.",
                description=f"{base.randint(5, 50)} udziałów", value=_money(base.uniform(5_000, 150_000)),
            ))
        if base.random() < 0.8:
            car_year = base.randint(2012, 2022)
            items.append(ExtractedItem(
                category=AssetCategory.VEHICLE, label="Samochód osobowy",
                description=f"{base.choice(CARS)}, rocznik {car_year}",
                value=_money(base.uniform(35_000, 140_000) * 0.9**years, 1000),
            ))

        salary = base.uniform(165_000, 205_000) * (1 + 0.06 * years)
        items.append(ExtractedItem(
            category=AssetCategory.INCOME, label="Uposażenie i dieta poselska",
            description=f"za rok {context.year}", value=_money(salary),
        ))
        if base.random() < 0.3:
            items.append(ExtractedItem(
                category=AssetCategory.INCOME, label="Umowy o dzieło i wynajem",
                value=_money(base.uniform(8_000, 90_000) * year_rng.uniform(0.6, 1.3)),
            ))

        if base.random() < 0.45:
            principal = base.uniform(120_000, 650_000) * max(0.2, 1 - 0.09 * years)
            items.append(ExtractedItem(
                category=AssetCategory.LIABILITY, label="Kredyt hipoteczny",
                description=f"{base.choice(BANKS)}, na zakup mieszkania", value=_money(principal),
            ))

        confidence = round(year_rng.uniform(0.58, 0.98), 2)
        return ExtractedDeclaration(
            items=items,
            confidence=confidence,
            notes="Dane demonstracyjne wygenerowane przez MockExtractor (nie pochodzą z PDF).",
            model="mock",
            prompt_version=self.prompt_version,
        )
