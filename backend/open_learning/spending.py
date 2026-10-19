"""Independent PostgreSQL transactions for cloud spending admission and settlement."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation, localcontext

from sqlalchemy import select, text

from .models import CloudAttempt, CloudBudget, CloudSettlement, ident, now

QUANTUM = Decimal("0.000000000001")
MAX_AMOUNT = Decimal("1e18")


class SpendingError(ValueError):
    pass


def money(value, *, round_up=False) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise SpendingError("Invalid cloud monetary value")
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount >= MAX_AMOUNT:
            raise SpendingError("Invalid cloud monetary value")
        with localcontext() as context:
            context.prec = 60
            rounded = amount.quantize(QUANTUM, rounding=ROUND_CEILING)
        if not round_up and rounded != amount:
            raise SpendingError("Cloud monetary value exceeds ledger precision")
        return rounded
    except (InvalidOperation, TypeError, ValueError) as error:
        if isinstance(error, SpendingError):
            raise
        raise SpendingError("Invalid cloud monetary value") from None


def reserve_amount(
    context_length: int,
    output_tokens: int,
    prompt_rate: Decimal,
    completion_rate: Decimal,
    request_fee_cap: Decimal,
) -> Decimal:
    if (
        type(context_length) is not int
        or type(output_tokens) is not int
        or context_length <= 0
        or output_tokens <= 0
    ):
        raise SpendingError("Invalid cloud token bounds")
    with localcontext() as context:
        context.prec = 60
        return money(
            context_length * money(prompt_rate)
            + output_tokens * money(completion_rate)
            + money(request_fee_cap),
            round_up=True,
        )


@dataclass(frozen=True)
class Quote:
    model: str
    provider_tag: str
    provider_name: str
    provider_name_unique: bool
    output_parameter: str
    context_length: int
    output_tokens: int
    prompt_rate: Decimal
    completion_rate: Decimal
    request_fee_cap: Decimal
    metadata_sha256: str
    checked_at: datetime

    @property
    def reservation(self) -> Decimal:
        return reserve_amount(
            self.context_length,
            self.output_tokens,
            self.prompt_rate,
            self.completion_rate,
            self.request_fee_cap,
        )


def _require_postgres(factory):
    if (
        factory is None
        or factory.kw.get("bind") is None
        or factory.kw["bind"].dialect.name != "postgresql"
    ):
        raise SpendingError("Cloud spending requires a PostgreSQL session factory")


def _lock_budget(db):
    budget = db.scalar(select(CloudBudget).where(CloudBudget.id == 1).with_for_update())
    if budget is None:
        raise SpendingError("Cloud budget migration is missing")
    return budget


def reserve_attempt(
    factory, settings, quote: Quote, key_remaining: Decimal, request_bytes: int
) -> str:
    """Return only after the reservation has committed; any ambiguity prevents dispatch."""
    _require_postgres(factory)
    amount = quote.reservation
    if amount > settings.openrouter_request_budget_usd:
        raise SpendingError("Cloud request budget exceeded")
    if request_bytes <= 0 or request_bytes > 256 * 1024:
        raise SpendingError("Cloud request size exceeded")
    attempt_id = ident()
    with factory.begin() as db:
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        budget = _lock_budget(db)
        if budget.lifetime_limit is None:
            budget.lifetime_limit = settings.openrouter_lifetime_budget_usd
        elif budget.lifetime_limit != settings.openrouter_lifetime_budget_usd:
            raise SpendingError("Cloud lifetime budget differs from persisted limit")
        if budget.debt != 0 or budget.spent + budget.held + amount > budget.lifetime_limit:
            raise SpendingError("Cloud lifetime budget unavailable")
        if money(key_remaining) < budget.held + amount:
            raise SpendingError("Cloud key limit remaining is insufficient")
        checked = quote.checked_at
        if checked.tzinfo is None or not datetime.now(UTC) - timedelta(
            seconds=30
        ) <= checked <= datetime.now(UTC):
            raise SpendingError("Cloud metadata is stale")
        db.add(
            CloudAttempt(
                id=attempt_id,
                budget_id=1,
                key_sha256=hashlib.sha256(
                    settings.openrouter_api_key.get_secret_value().encode()
                ).hexdigest(),
                model=quote.model,
                provider_tag=quote.provider_tag,
                provider_name=quote.provider_name,
                provider_name_unique=quote.provider_name_unique,
                context_length=quote.context_length,
                output_tokens=quote.output_tokens,
                prompt_rate=quote.prompt_rate,
                completion_rate=quote.completion_rate,
                request_fee_cap=quote.request_fee_cap,
                reserved=amount,
                metadata_sha256=quote.metadata_sha256,
                metadata_checked_at=checked,
                request_bytes=request_bytes,
            )
        )
        budget.held += amount
        budget.updated_at = now()
    return attempt_id


def record_generation(factory, attempt_id: str, generation_id: str):
    _require_postgres(factory)
    if not isinstance(generation_id, str) or not generation_id or len(generation_id) > 200:
        raise SpendingError("Invalid cloud generation ID")
    with factory.begin() as db:
        _lock_budget(db)
        attempt = db.scalar(
            select(CloudAttempt).where(CloudAttempt.id == attempt_id).with_for_update()
        )
        if attempt is None or (
            attempt.generation_id is not None and attempt.generation_id != generation_id
        ):
            raise SpendingError("Cloud generation identity mismatch")
        attempt.generation_id = generation_id


def settle_attempt(factory, attempt_id: str, generation_id: str, actual) -> bool:
    """Settle once; a duplicate identical settlement is harmless, discrepancies fail closed."""
    _require_postgres(factory)
    cost = money(actual, round_up=True)
    with factory.begin() as db:
        budget = _lock_budget(db)
        attempt = db.scalar(
            select(CloudAttempt).where(CloudAttempt.id == attempt_id).with_for_update()
        )
        if attempt is None or attempt.generation_id != generation_id:
            raise SpendingError("Cloud generation identity mismatch")
        existing = db.get(CloudSettlement, attempt_id)
        if existing is not None:
            if existing.actual != cost or existing.generation_id != generation_id:
                raise SpendingError("Cloud settlement differs from recorded cost")
            return False
        budget.held -= attempt.reserved
        budget.spent += cost
        if cost > attempt.reserved:
            budget.debt += cost - attempt.reserved
        budget.updated_at = now()
        db.add(CloudSettlement(attempt_id=attempt_id, generation_id=generation_id, actual=cost))
    return True
