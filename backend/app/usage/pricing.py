from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_MILLION = Decimal("1000000")
_DATABASE_SCALE = Decimal("0.000001")


def _as_non_negative_decimal(value: Decimal, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field_name} 必须是非负有限 Decimal")
    return value


def _as_non_negative_token_count(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} 必须是非负整数")
    return value


def _money(value: Decimal) -> Decimal:
    return value.quantize(_DATABASE_SCALE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ModelPricing:
    """每百万 Token 的不可变价格快照。"""

    cache_hit_input_per_million: Decimal
    cache_miss_input_per_million: Decimal
    output_per_million: Decimal

    def __post_init__(self) -> None:
        for field_name in (
            "cache_hit_input_per_million",
            "cache_miss_input_per_million",
            "output_per_million",
        ):
            _as_non_negative_decimal(getattr(self, field_name), field_name)

    def snapshot(self) -> dict[str, str]:
        return {
            "unit": "per_million_tokens",
            "cache_hit_input": str(self.cache_hit_input_per_million),
            "cache_miss_input": str(self.cache_miss_input_per_million),
            "output": str(self.output_per_million),
        }


def calculate_cost(
    pricing: ModelPricing,
    *,
    cache_hit_input_tokens: int,
    cache_miss_input_tokens: int,
    output_tokens: int,
) -> Decimal:
    cache_hit_input_tokens = _as_non_negative_token_count(
        cache_hit_input_tokens, "cache_hit_input_tokens"
    )
    cache_miss_input_tokens = _as_non_negative_token_count(
        cache_miss_input_tokens, "cache_miss_input_tokens"
    )
    output_tokens = _as_non_negative_token_count(output_tokens, "output_tokens")
    cost = (
        Decimal(cache_hit_input_tokens) * pricing.cache_hit_input_per_million
        + Decimal(cache_miss_input_tokens) * pricing.cache_miss_input_per_million
        + Decimal(output_tokens) * pricing.output_per_million
    ) / _MILLION
    return _money(cost)


def calculate_reservation(
    pricing: ModelPricing,
    *,
    input_tokens: int,
    max_output_tokens: int,
) -> Decimal:
    """按较高输入价格保守预留，避免事前未知缓存命中比例造成低估。"""
    input_tokens = _as_non_negative_token_count(input_tokens, "input_tokens")
    max_output_tokens = _as_non_negative_token_count(max_output_tokens, "max_output_tokens")
    input_price = max(
        pricing.cache_hit_input_per_million,
        pricing.cache_miss_input_per_million,
    )
    return _money(
        (
            Decimal(input_tokens) * input_price
            + Decimal(max_output_tokens) * pricing.output_per_million
        )
        / _MILLION
    )
