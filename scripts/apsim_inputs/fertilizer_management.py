"""Validation helpers for APSIM fertilizer budgets and split applications."""

from __future__ import annotations

from datetime import datetime
from math import isclose


def entry_amount_kg_ha(entry: dict, total_n_kg_ha: float) -> float:
    """Resolve one application without silently mixing amount and fraction."""
    has_amount = "amount_kg_ha" in entry
    has_fraction = "fraction_of_total_n" in entry
    if has_amount == has_fraction:
        raise ValueError("Each N application needs exactly one of amount_kg_ha or fraction_of_total_n")
    if has_fraction:
        fraction = float(entry["fraction_of_total_n"])
        if not 0.0 <= fraction <= 1.0:
            raise ValueError(f"fraction_of_total_n must lie within [0, 1], got {fraction}")
        return total_n_kg_ha * fraction
    amount = float(entry["amount_kg_ha"])
    if amount < 0.0:
        raise ValueError(f"Fertilizer amount cannot be negative, got {amount}")
    return amount


def validate_crop_n_budget(crop: str, values: dict, tolerance: float = 1e-3) -> dict:
    """Require the declared crop total to equal sowing plus topdress N."""
    total = float(values["total_n_kg_ha"])
    sowing = float(values["sowing_n_kg_ha"])
    if total < 0.0 or sowing < 0.0:
        raise ValueError(f"{crop}: N amounts cannot be negative")
    topdress = values.get("topdress_n", [])
    resolved = []
    for entry in topdress:
        datetime.strptime(entry["month_day"], "%m-%d")
        resolved.append(entry_amount_kg_ha(entry, total))
    scheduled = sowing + sum(resolved)
    if not isclose(scheduled, total, abs_tol=tolerance, rel_tol=0.0):
        raise ValueError(
            f"{crop}: declared total_n_kg_ha={total:g}, but sowing+topdress={scheduled:g} kg N/ha"
        )
    return {
        "crop": crop,
        "total_n_kg_ha": total,
        "sowing_n_kg_ha": sowing,
        "topdress_n_kg_ha": resolved,
        "scheduled_n_kg_ha": scheduled,
    }


def validate_scenario_n_budgets(scenario: dict) -> dict[str, dict]:
    return {crop: validate_crop_n_budget(crop, scenario[crop]) for crop in ("wheat", "maize")}
