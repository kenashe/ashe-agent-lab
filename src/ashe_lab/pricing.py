"""Cost estimation from a hand-maintained, dated price table.

Three honesty rules govern this module, because a wrong cost number is worse
than no cost number:

1.  **Every entry is dated.** Prices change. An estimate carries the
    ``as_of`` date of the rate used, so a reader can judge whether it is stale.
2.  **Unknown means null, never zero.** A model absent from the table produces
    ``cost_usd: null`` plus a ``cost_note`` saying why. Reports show
    "unknown", and aggregate cost is reported as a floor with a count of
    unpriced trials.
3.  **Nothing here is authoritative.** These are published list prices entered
    by hand. Treat the output as a planning estimate, not an invoice. Your real
    bill comes from your provider.

The table is intentionally small and easy to edit. Keeping prices in code
rather than fetching them at run time means an experiment run offline in 2031
still produces the same numbers it produced today.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

#: Prices in USD per 1,000,000 tokens: (input_rate, output_rate, as_of_date).
#: Keyed by (provider_name, model_id_prefix). Longest matching prefix wins, so
#: "gpt-4o-mini" is matched before "gpt-4o".
#:
#: These figures are published list prices recorded on the dates shown. Verify
#: against your provider's current pricing page before quoting them anywhere
#: that matters.
PRICE_TABLE: Dict[Tuple[str, str], Tuple[float, float, str]] = {
    ("echo", "echo"): (0.0, 0.0, "2026-09-18"),
    ("failing", "failing"): (0.0, 0.0, "2026-09-18"),
    # --- OpenAI-compatible ---------------------------------------------
    ("openai_chat", "gpt-4o-mini"): (0.15, 0.60, "2026-09-18"),
    ("openai_chat", "gpt-4o"): (2.50, 10.00, "2026-09-18"),
    ("openai_chat", "gpt-4.1-mini"): (0.40, 1.60, "2026-09-18"),
    ("openai_chat", "gpt-4.1"): (2.00, 8.00, "2026-09-18"),
    ("openai_chat", "o4-mini"): (1.10, 4.40, "2026-09-18"),
    # --- Anthropic ------------------------------------------------------
    ("anthropic_messages", "claude-3-5-haiku"): (0.80, 4.00, "2026-09-18"),
    ("anthropic_messages", "claude-3-5-sonnet"): (3.00, 15.00, "2026-09-18"),
    ("anthropic_messages", "claude-sonnet-4"): (3.00, 15.00, "2026-09-18"),
    ("anthropic_messages", "claude-opus-4"): (15.00, 75.00, "2026-09-18"),
}

#: Documented so a reader knows the unit without reading the code.
PRICE_UNIT = "USD per 1,000,000 tokens"


def lookup_rates(provider: str, model: str) -> Optional[Tuple[float, float, str]]:
    """Find rates for ``(provider, model)`` by longest prefix match."""
    best: Optional[Tuple[float, float, str]] = None
    best_length = -1
    for (table_provider, prefix), rates in PRICE_TABLE.items():
        if table_provider != provider:
            continue
        if model.startswith(prefix) and len(prefix) > best_length:
            best = rates
            best_length = len(prefix)
    return best


def estimate_cost(
    provider: str,
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
) -> Dict[str, Any]:
    """Estimate the USD cost of one call.

    Returns a dict that is written verbatim into the trial record::

        {
          "cost_usd": 0.000123 | None,
          "cost_note": "..." ,
          "rate_as_of": "2026-09-18" | None,
          "input_rate_per_mtok": 0.15 | None,
          "output_rate_per_mtok": 0.60 | None,
        }
    """
    rates = lookup_rates(provider, model)
    if rates is None:
        return {
            "cost_usd": None,
            "cost_note": (
                "no price entry for provider={0!r} model={1!r}; add one to "
                "src/ashe_lab/pricing.py to enable cost estimation".format(provider, model)
            ),
            "rate_as_of": None,
            "input_rate_per_mtok": None,
            "output_rate_per_mtok": None,
        }

    input_rate, output_rate, as_of = rates

    if input_tokens is None and output_tokens is None:
        return {
            "cost_usd": None,
            "cost_note": "provider reported no token usage, so cost cannot be computed",
            "rate_as_of": as_of,
            "input_rate_per_mtok": input_rate,
            "output_rate_per_mtok": output_rate,
        }

    note_parts = []
    effective_input = input_tokens
    effective_output = output_tokens
    if effective_input is None:
        effective_input = 0
        note_parts.append("input tokens unreported, counted as 0")
    if effective_output is None:
        effective_output = 0
        note_parts.append("output tokens unreported, counted as 0")

    cost = (effective_input / 1_000_000.0) * input_rate + (
        effective_output / 1_000_000.0
    ) * output_rate

    note = "estimated from list prices as of {0}".format(as_of)
    if note_parts:
        note = note + " (" + "; ".join(note_parts) + ")"

    return {
        "cost_usd": round(cost, 8),
        "cost_note": note,
        "rate_as_of": as_of,
        "input_rate_per_mtok": input_rate,
        "output_rate_per_mtok": output_rate,
    }


def priced_models() -> Dict[str, Any]:
    """The whole table, rendered for display by ``ashe-lab list pricing``."""
    return {
        "unit": PRICE_UNIT,
        "entries": [
            {
                "provider": provider,
                "model_prefix": prefix,
                "input_rate": rates[0],
                "output_rate": rates[1],
                "as_of": rates[2],
            }
            for (provider, prefix), rates in sorted(PRICE_TABLE.items())
        ],
    }
