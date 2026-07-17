from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.quotas.service import QuotaDefaults, effective_limit, shanghai_date, validate_cost


def test_shanghai_date_uses_fixed_timezone_at_daily_boundary() -> None:
    assert shanghai_date(datetime(2026, 7, 17, 15, 59, tzinfo=UTC)) == datetime(2026, 7, 17).date()
    assert shanghai_date(datetime(2026, 7, 17, 16, 0, tzinfo=UTC)) == datetime(2026, 7, 18).date()


def test_personal_quota_override_falls_back_to_stage4_default() -> None:
    defaults = QuotaDefaults(daily_questions=50, daily_uploads=20, storage_bytes=500 * 1024**2)

    assert effective_limit(None, defaults.daily_questions) == 50
    assert effective_limit(3, defaults.daily_questions) == 3


def test_cost_must_be_non_negative_finite_decimal_without_float() -> None:
    assert validate_cost(Decimal("20.00")) == Decimal("20.00")
    with pytest.raises(ValueError):
        validate_cost(20.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        validate_cost(Decimal("-0.01"))
