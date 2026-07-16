from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.contracts import ChatCompletion, ChatUsage
from app.db.models import LlmUsageEvent
from app.usage.pricing import ModelPricing, calculate_cost, calculate_reservation


@dataclass(frozen=True)
class UsageSettlement:
    status: Literal[
        "succeeded",
        "usage_unknown",
        "failed_before_request",
        "failed_after_request",
    ]
    settled_cost: Decimal
    released_cost: Decimal
    usage_complete: bool


def _validate_reserved_cost(reserved_cost: Decimal) -> None:
    if not isinstance(reserved_cost, Decimal) or not reserved_cost.is_finite() or reserved_cost < 0:
        raise ValueError("reserved_cost 必须是非负有限 Decimal")


def _real_cost(
    pricing: ModelPricing,
    reserved_cost: Decimal,
    usage: ChatUsage,
) -> tuple[Decimal, Decimal]:
    settled = calculate_cost(
        pricing,
        cache_hit_input_tokens=usage.cache_hit_input_tokens,
        cache_miss_input_tokens=usage.cache_miss_input_tokens,
        output_tokens=usage.output_tokens,
    )
    return settled, max(Decimal("0.000000"), reserved_cost - settled)


def settle_after_response(
    *,
    pricing: ModelPricing,
    reserved_cost: Decimal,
    usage: ChatUsage | None,
) -> UsageSettlement:
    _validate_reserved_cost(reserved_cost)
    if usage is None or not usage.is_complete:
        return UsageSettlement(
            status="usage_unknown",
            settled_cost=reserved_cost,
            released_cost=Decimal("0.000000"),
            usage_complete=False,
        )
    settled, released = _real_cost(pricing, reserved_cost, usage)
    return UsageSettlement("succeeded", settled, released, True)


def settle_after_failure(
    *,
    pricing: ModelPricing,
    reserved_cost: Decimal,
    request_started: bool,
    usage: ChatUsage | None,
) -> UsageSettlement:
    _validate_reserved_cost(reserved_cost)
    if not request_started:
        return UsageSettlement(
            status="failed_before_request",
            settled_cost=Decimal("0.000000"),
            released_cost=reserved_cost,
            usage_complete=False,
        )
    if usage is not None and usage.is_complete:
        settled, released = _real_cost(pricing, reserved_cost, usage)
        return UsageSettlement("failed_after_request", settled, released, True)
    return UsageSettlement(
        status="failed_after_request",
        settled_cost=reserved_cost,
        released_cost=Decimal("0.000000"),
        usage_complete=False,
    )


def create_usage_reservation(
    *,
    user_id: UUID,
    knowledge_base_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    purpose: Literal["rewrite", "answer"],
    model: str,
    pricing: ModelPricing,
    reserved_cost: Decimal,
) -> LlmUsageEvent:
    return LlmUsageEvent(
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        conversation_id=conversation_id,
        message_id=message_id,
        purpose=purpose,
        status="reserved",
        model=model,
        price_snapshot=pricing.snapshot(),
        reserved_cost=reserved_cost,
        settled_cost=None,
    )


def apply_settlement(
    event: LlmUsageEvent,
    *,
    settlement: UsageSettlement,
    usage: ChatUsage | None,
    provider_request_id: str | None,
    finish_reason: str | None,
    error_code: str | None,
    duration_ms: int | None,
) -> None:
    event.status = settlement.status
    event.settled_cost = settlement.settled_cost
    event.usage_complete = settlement.usage_complete
    if usage is not None:
        event.cache_hit_input_tokens = usage.cache_hit_input_tokens
        event.cache_miss_input_tokens = usage.cache_miss_input_tokens
        event.output_tokens = usage.output_tokens
        event.reasoning_tokens = usage.reasoning_tokens
        event.total_tokens = usage.total_tokens
    event.provider_request_id = provider_request_id
    event.finish_reason = finish_reason
    event.error_code = error_code
    event.duration_ms = duration_ms
    event.completed_at = datetime.now(UTC)


class ConversationUsageRecorder:
    """为一次会话回答记录对应的独立改写模型调用。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        user_id: UUID,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        model: str,
        pricing: ModelPricing,
        rewrite_input_tokens: int,
        rewrite_max_output_tokens: int,
    ) -> None:
        self._session_factory = session_factory
        self._user_id = user_id
        self._knowledge_base_id = knowledge_base_id
        self._conversation_id = conversation_id
        self._message_id = message_id
        self._model = model
        self._pricing = pricing
        self._rewrite_input_tokens = rewrite_input_tokens
        self.rewrite_max_output_tokens = rewrite_max_output_tokens
        self._rewrite_usage_id: UUID | None = None

    async def before_rewrite_request(self) -> None:
        if self._rewrite_usage_id is not None:
            raise RuntimeError("同一回答不能重复预留改写调用")
        reserved_cost = calculate_reservation(
            self._pricing,
            input_tokens=self._rewrite_input_tokens,
            max_output_tokens=self.rewrite_max_output_tokens,
        )
        async with self._session_factory.begin() as session:
            event = create_usage_reservation(
                user_id=self._user_id,
                knowledge_base_id=self._knowledge_base_id,
                conversation_id=self._conversation_id,
                message_id=self._message_id,
                purpose="rewrite",
                model=self._model,
                pricing=self._pricing,
                reserved_cost=reserved_cost,
            )
            session.add(event)
            await session.flush()
            self._rewrite_usage_id = event.id

    async def rewrite_completed(self, completion: ChatCompletion) -> None:
        await self._finalize_rewrite(
            completion=completion,
            request_started=True,
            error_code=None,
        )

    async def rewrite_failed(
        self,
        request_started: bool,
        error_code: str,
        completion: ChatCompletion | None = None,
    ) -> None:
        await self._finalize_rewrite(
            completion=completion,
            request_started=request_started,
            error_code=error_code,
        )

    async def _finalize_rewrite(
        self,
        *,
        completion: ChatCompletion | None,
        request_started: bool,
        error_code: str | None,
    ) -> None:
        usage_id = self._rewrite_usage_id
        if usage_id is None:
            return
        async with self._session_factory.begin() as session:
            event = await session.get(LlmUsageEvent, usage_id, with_for_update=True)
            if event is None or event.status != "reserved":
                return
            if completion is not None and error_code is None:
                settlement = settle_after_response(
                    pricing=self._pricing,
                    reserved_cost=event.reserved_cost,
                    usage=completion.usage,
                )
            else:
                settlement = settle_after_failure(
                    pricing=self._pricing,
                    reserved_cost=event.reserved_cost,
                    request_started=request_started,
                    usage=completion.usage if completion is not None else None,
                )
            apply_settlement(
                event,
                settlement=settlement,
                usage=completion.usage if completion is not None else None,
                provider_request_id=(
                    completion.provider_request_id if completion is not None else None
                ),
                finish_reason=completion.finish_reason if completion is not None else None,
                error_code=error_code,
                duration_ms=None,
            )
