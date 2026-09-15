"""Deterministic fault modes for the synthetic target.

A fault mode is chosen once at process start (CLI flag / env var / ``create_app`` argument) and
cannot be changed through the UI, so an automated agent can never flip it.
"""

from enum import StrEnum


class FaultMode(StrEnum):
    # Member detail renders TWO rows whose row header is exactly "Savings" (a second savings
    # account with a different balance). Every semantic locator strategy then matches twice, so
    # a replay engine must fail closed with AMBIGUOUS_TARGET rather than pick one.
    AMBIGUOUS_SAVINGS = "ambiguous_savings"
