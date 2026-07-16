from decimal import Decimal

from app.ai.contracts import ChatUsage
from app.usage.pricing import ModelPricing, calculate_cost, calculate_reservation
from app.usage.service import settle_after_failure, settle_after_response


def pricing() -> ModelPricing:
    return ModelPricing(
        cache_hit_input_per_million=Decimal("0.25"),
        cache_miss_input_per_million=Decimal("1.00"),
        output_per_million=Decimal("2.00"),
    )


def test_calculates_each_token_class_with_decimal_per_million_prices() -> None:
    result = calculate_cost(
        pricing(),
        cache_hit_input_tokens=1_000_000,
        cache_miss_input_tokens=2_000_000,
        output_tokens=3_000_000,
    )

    assert result == Decimal("8.250000")
    assert isinstance(result, Decimal)


def test_reservation_uses_conservative_input_price_and_maximum_output() -> None:
    result = calculate_reservation(
        pricing(),
        input_tokens=1_000_000,
        max_output_tokens=500_000,
    )

    assert result == Decimal("2.000000")


def test_complete_usage_settles_real_cost_and_releases_difference() -> None:
    usage = ChatUsage(
        cache_hit_input_tokens=400_000,
        cache_miss_input_tokens=100_000,
        output_tokens=200_000,
        reasoning_tokens=50_000,
        total_tokens=700_000,
        is_complete=True,
    )

    settlement = settle_after_response(
        pricing=pricing(),
        reserved_cost=Decimal("2.000000"),
        usage=usage,
    )

    assert settlement.status == "succeeded"
    assert settlement.settled_cost == Decimal("0.600000")
    assert settlement.released_cost == Decimal("1.400000")
    assert settlement.usage_complete is True


def test_missing_or_incomplete_usage_keeps_the_entire_reservation() -> None:
    missing = settle_after_response(
        pricing=pricing(),
        reserved_cost=Decimal("2.000000"),
        usage=None,
    )
    incomplete = settle_after_response(
        pricing=pricing(),
        reserved_cost=Decimal("2.000000"),
        usage=ChatUsage(1, 0, 0, 0, 1, False),
    )

    assert missing.status == incomplete.status == "usage_unknown"
    assert missing.settled_cost == incomplete.settled_cost == Decimal("2.000000")
    assert missing.released_cost == incomplete.released_cost == Decimal("0.000000")
    assert missing.usage_complete is incomplete.usage_complete is False


def test_failure_before_provider_request_releases_the_entire_reservation() -> None:
    settlement = settle_after_failure(
        pricing=pricing(),
        reserved_cost=Decimal("2.000000"),
        request_started=False,
        usage=None,
    )

    assert settlement.status == "failed_before_request"
    assert settlement.settled_cost == Decimal("0.000000")
    assert settlement.released_cost == Decimal("2.000000")


def test_failure_after_provider_request_uses_complete_usage_or_keeps_reservation() -> None:
    usage = ChatUsage(0, 100_000, 200_000, 0, 300_000, True)

    complete = settle_after_failure(
        pricing=pricing(),
        reserved_cost=Decimal("2.000000"),
        request_started=True,
        usage=usage,
    )
    unknown = settle_after_failure(
        pricing=pricing(),
        reserved_cost=Decimal("2.000000"),
        request_started=True,
        usage=None,
    )

    assert complete.status == unknown.status == "failed_after_request"
    assert complete.settled_cost == Decimal("0.500000")
    assert complete.released_cost == Decimal("1.500000")
    assert complete.usage_complete is True
    assert unknown.settled_cost == Decimal("2.000000")
    assert unknown.released_cost == Decimal("0.000000")
    assert unknown.usage_complete is False
