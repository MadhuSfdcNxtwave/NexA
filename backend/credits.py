"""Credit metering from BigQuery bytes scanned."""
from __future__ import annotations

import config
from db import UsageLog, User


def credits_for_bytes(bytes_estimate: int | None, *, from_cache: bool = False) -> float:
    if from_cache or not bytes_estimate or bytes_estimate <= 0:
        return 0.0
    gb = bytes_estimate / (1024**3)
    return round(gb * config.CREDITS_PER_GB, 4)


def apply_usage_charge(
    db,
    user: User,
    *,
    bytes_estimate: int | None,
    from_cache: bool,
    project_id: int | None,
    action: str,
    detail: str = "",
) -> tuple[float, float]:
    """Deduct credits and log usage. Returns (credits_used, credits_remaining)."""
    used = credits_for_bytes(bytes_estimate, from_cache=from_cache)
    if user.role != "admin" and used > 0 and user.credits_balance < used:
        raise ValueError(
            f"Insufficient credits (need {used:.4f}, have {user.credits_balance:.4f}). "
            "Contact your admin for more."
        )
    if user.role != "admin" and used > 0:
        user.credits_balance = round(user.credits_balance - used, 4)
    remaining = user.credits_balance
    db.add(
        UsageLog(
            user_id=user.id,
            project_id=project_id,
            action=action,
            bytes_estimate=bytes_estimate or 0,
            credits_used=used,
            detail=(detail or "")[:500],
        )
    )
    return used, remaining


def enrich_result_with_credits(result: dict, used: float, remaining: float) -> dict:
    result["credits_used"] = used
    result["credits_remaining"] = remaining
    return result
