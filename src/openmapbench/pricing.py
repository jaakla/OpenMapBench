from __future__ import annotations

from dataclasses import dataclass

from .models import CostEstimate, TokenUsage

PRICING_AS_OF = "2026-08-30"
ESTIMATE_NOTE = (
    "API-equivalent list-price estimate only; it is not an actual ChatGPT subscription charge."
)


@dataclass(frozen=True)
class ModelPricing:
    model: str
    input: float
    cached_input: float
    cache_write_input: float
    output: float
    source: str

    @property
    def rates(self) -> dict[str, float]:
        return {
            "input": self.input,
            "cached_input": self.cached_input,
            "cache_write_input": self.cache_write_input,
            "output": self.output,
        }


MODEL_PRICING = {
    "gpt-5.6-luna": ModelPricing(
        model="gpt-5.6-luna",
        input=0.20,
        cached_input=0.02,
        cache_write_input=0.25,
        output=1.20,
        source="https://developers.openai.com/api/docs/models/gpt-5.6-luna",
    ),
    "gpt-5.6-terra": ModelPricing(
        model="gpt-5.6-terra",
        input=2.00,
        cached_input=0.20,
        cache_write_input=2.50,
        output=12.00,
        source="https://developers.openai.com/api/docs/models/gpt-5.6-terra",
    ),
    "gpt-5.6-sol": ModelPricing(
        model="gpt-5.6-sol",
        input=4.00,
        cached_input=0.40,
        cache_write_input=5.00,
        output=20.00,
        source="https://developers.openai.com/api/docs/models/gpt-5.6-sol",
    ),
}


def estimate_cost(usage: TokenUsage) -> CostEstimate | None:
    """Estimate API-equivalent cost using a dated, explicit model price catalog."""
    if not usage.model:
        return None
    pricing = MODEL_PRICING.get(usage.model.lower())
    if pricing is None:
        return None

    if usage.input_tokens is not None and usage.output_tokens is not None:
        cached = usage.cached_input_tokens or 0
        cache_write = usage.cache_write_input_tokens or 0
        if cached + cache_write <= usage.input_tokens:
            uncached = usage.input_tokens - cached - cache_write
            cost = (
                uncached * pricing.input
                + cached * pricing.cached_input
                + cache_write * pricing.cache_write_input
                + usage.output_tokens * pricing.output
            ) / 1_000_000
            cost = round(cost, 9)
            return CostEstimate(
                model=pricing.model,
                basis="token_breakdown",
                estimated_cost_usd=cost,
                minimum_cost_usd=cost,
                maximum_cost_usd=cost,
                pricing_as_of=PRICING_AS_OF,
                pricing_source=pricing.source,
                rates_per_million_usd=pricing.rates,
                note=ESTIMATE_NOTE,
            )

    rates = pricing.rates.values()
    minimum = round(usage.total_tokens * min(rates) / 1_000_000, 9)
    maximum = round(usage.total_tokens * max(rates) / 1_000_000, 9)
    return CostEstimate(
        model=pricing.model,
        basis="total_tokens_range",
        minimum_cost_usd=minimum,
        maximum_cost_usd=maximum,
        pricing_as_of=PRICING_AS_OF,
        pricing_source=pricing.source,
        rates_per_million_usd=pricing.rates,
        note=(
            f"{ESTIMATE_NOTE} Only total tokens were available, so the range assumes all "
            "tokens used the cheapest or most expensive applicable rate."
        ),
    )
