from __future__ import annotations

import calendar
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc

from .config import settings
from .database import get_db
from .models import User, Subscription, Payment, GatewayPlan
from .security import require_verified_user

router = APIRouter(prefix="/api/billing", tags=["billing"])


class CheckoutInput(BaseModel):
    tier: Literal["advanced"]
    currency: Literal["USD", "GHS"]
    payment_method: Literal["card", "mobile_money", "bank_transfer"]


class FreePlanInput(BaseModel):
    tier: Literal["normal", "professional"]


def _money_to_minor(value: str) -> int | None:
    if not value or not value.strip():
        return None
    try:
        amount = Decimal(value.strip())
    except InvalidOperation:
        return None
    if amount <= 0 or amount.as_tuple().exponent < -2:
        return None
    return int(amount * 100)


def _minor_to_major(value: int) -> str:
    return f"{Decimal(value) / Decimal(100):.2f}"


def _price(tier: str, currency: str) -> int | None:
    field = f"{tier}_price_{currency.lower()}"
    configured = getattr(settings, field, "")
    if tier == "advanced" and currency == "USD" and not str(configured or "").strip():
        configured = "10.00"
    return _money_to_minor(str(configured or ""))


def _month_after(value: datetime) -> datetime:
    month = value.month + 1
    year = value.year
    if month == 13:
        month = 1
        year += 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _currency_enabled(currency: str) -> bool:
    if currency == "GHS":
        return settings.flutterwave_ghs_enabled
    return settings.flutterwave_usd_enabled


def _available_methods(currency: str) -> list[str]:
    if currency == "USD":
        return ["card"] if settings.flutterwave_usd_enabled else []
    if not settings.flutterwave_ghs_enabled:
        return []
    methods = ["card", "mobile_money"]
    if settings.flutterwave_ghs_bank_transfer_enabled:
        methods.append("bank_transfer")
    return methods


def _ensure_gateway_ready() -> None:
    if not settings.flutterwave_secret_key:
        raise HTTPException(status_code=503, detail="Payment checkout is not configured yet. The developer must add FLUTTERWAVE_SECRET_KEY to the private backend environment.")


async def _flw_request(method: str, path: str, *, params: dict | None = None, payload: dict | None = None) -> dict[str, Any]:
    _ensure_gateway_ready()
    url = settings.flutterwave_api_base.rstrip("/") + path
    async with httpx.AsyncClient(timeout=35) as client:
        response = await client.request(
            method,
            url,
            headers={"Authorization": f"Bearer {settings.flutterwave_secret_key}", "Content-Type": "application/json"},
            params=params,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    if data.get("status") == "error":
        raise RuntimeError(data.get("message") or "Flutterwave rejected the request")
    return data


async def _get_or_create_plan(db: Session, tier: str, currency: str, amount_minor: int) -> GatewayPlan:
    plan = db.query(GatewayPlan).filter_by(
        provider="flutterwave", tier=tier, currency=currency,
        amount_minor=amount_minor, interval="monthly"
    ).first()
    if plan:
        return plan
    response = await _flw_request("POST", "/payment-plans", payload={
        "amount": float(_minor_to_major(amount_minor)),
        "name": f"PlanSphere AI {tier.title()} {currency} monthly",
        "interval": "monthly",
        "currency": currency,
    })
    data = response.get("data") or {}
    plan_id = data.get("id")
    if not plan_id:
        raise RuntimeError("Flutterwave did not return a payment plan ID")
    plan = GatewayPlan(
        provider="flutterwave", tier=tier, currency=currency,
        amount_minor=amount_minor, interval="monthly",
        provider_plan_id=str(plan_id),
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


async def _verify_transaction(transaction_id: str | int) -> dict[str, Any]:
    response = await _flw_request("GET", f"/transactions/{transaction_id}/verify")
    return response.get("data") or {}


async def _find_provider_subscription_id(user: User, provider_plan_id: str | None) -> str | None:
    if not provider_plan_id:
        return None
    try:
        response = await _flw_request(
            "GET", "/subscriptions",
            params={"email": user.email, "plan": provider_plan_id},
        )
        items = response.get("data") or []
        if isinstance(items, dict):
            items = [items]
        active = [item for item in items if str(item.get("status", "")).lower() not in ("cancelled", "deactivated", "disabled")]
        if active:
            return str(active[0].get("id")) if active[0].get("id") else None
    except Exception:
        return None
    return None


def _validate_verified_transaction(tx: dict[str, Any], payment: Payment) -> None:
    if str(tx.get("status", "")).lower() != "successful":
        raise HTTPException(status_code=402, detail="Payment has not been confirmed as successful by the payment provider.")
    if tx.get("tx_ref") != payment.provider_reference:
        raise HTTPException(status_code=400, detail="Payment reference does not match this checkout.")
    if str(tx.get("currency", "")).upper() != payment.currency:
        raise HTTPException(status_code=400, detail="Payment currency does not match the expected currency.")
    charged = tx.get("charged_amount", tx.get("amount"))
    try:
        if Decimal(str(charged)) < (Decimal(payment.amount_minor) / Decimal(100)):
            raise HTTPException(status_code=400, detail="The verified payment amount is lower than the required amount.")
    except InvalidOperation:
        raise HTTPException(status_code=400, detail="The payment provider returned an invalid amount.")


async def _activate_payment(db: Session, payment: Payment, tx: dict[str, Any]) -> dict[str, Any]:
    _validate_verified_transaction(tx, payment)
    if payment.status == "paid":
        return {"activated": True, "already_processed": True}
    now = datetime.now(timezone.utc)
    payment.status = "paid"
    payment.provider_transaction_id = str(tx.get("id") or "")
    payment.paid_at = now
    subscription = db.query(Subscription).filter_by(id=payment.subscription_id).first() if payment.subscription_id else None
    user = db.query(User).filter_by(id=payment.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Account associated with payment no longer exists.")
    if subscription:
        subscription.status = "active"
        subscription.current_period_start = now
        subscription.current_period_end = _month_after(now)
        subscription.updated_at = now
        if subscription.auto_renew and not subscription.provider_subscription_id:
            # Lookup is best-effort. The provider's webhook remains authoritative.
            subscription.provider_subscription_id = await _find_provider_subscription_id(user, subscription.provider_plan_id)
        user.tier = subscription.tier
    db.commit()
    return {"activated": True, "already_processed": False, "tier": user.tier}


@router.get("/catalog")
def billing_catalog():
    plans: dict[str, Any] = {}
    for tier in ("advanced",):
        plans[tier] = {
            "USD": _price(tier, "USD"),
            "GHS": _price(tier, "GHS"),
        }
    return {
        "provider": "Flutterwave",
        "currencies": [
            {"code": "USD", "enabled": settings.flutterwave_usd_enabled, "methods": _available_methods("USD"), "note": "USD collection is enabled only after the merchant account is approved for USD."},
            {"code": "GHS", "enabled": settings.flutterwave_ghs_enabled, "methods": _available_methods("GHS"), "note": "Ghana cedi checkout; card and Ghana mobile money are supported subject to account activation."},
        ],
        "plans": {
            "normal": {"USD": 0, "GHS": 0},
            "professional": {"USD": 0, "GHS": 0},
            **plans,
        },
        "billing_rules": {
            "card": "Automatic monthly renewal requires a configured Flutterwave monthly payment plan and a successful card authorisation.",
            "mobile_money": "Ghana mobile-money payments are one-time in this integration; users renew manually at the end of the paid month.",
            "bank_transfer": "Bank-transfer checkout is shown only after the merchant has enabled and obtained provider approval for that GHS channel; renewal is manual.",
            "professional": "Professional is free and does not require checkout.",
            "advanced": "Advanced is USD 10.00 per month. A GHS price must be configured explicitly; the app does not invent an exchange rate.",
        },
        "terms_version": settings.terms_version,
        "privacy_version": settings.privacy_version,
    }


@router.post("/select-free")
def select_free_plan(payload: FreePlanInput, user: User = Depends(require_verified_user), db: Session = Depends(get_db)):
    tier = payload.tier
    active = db.query(Subscription).filter_by(user_id=user.id, status="active").order_by(desc(Subscription.created_at)).first()
    if active and (not active.current_period_end or active.current_period_end > datetime.now(timezone.utc)):
        raise HTTPException(status_code=409, detail="Your paid subscription is still active. Wait until it ends before changing to a free plan.")
    if active:
        active.status = "expired"
    user.tier = tier
    db.commit()
    return {"message": f"Your {tier.title()} free plan is now active.", "user": {"id": user.id, "username": user.username, "email": user.email, "phone": user.phone, "tier": user.tier, "email_verified": user.email_verified, "phone_verified": user.phone_verified, "monthly_messages": user.monthly_messages, "usage_month": user.usage_month, "terms_version": user.terms_version, "privacy_version": user.privacy_version}}


@router.get("/status")
def billing_status(user: User = Depends(require_verified_user), db: Session = Depends(get_db)):
    sub = db.query(Subscription).filter_by(user_id=user.id).order_by(desc(Subscription.created_at)).first()
    payments = db.query(Payment).filter_by(user_id=user.id).order_by(desc(Payment.created_at)).limit(10).all()
    return {
        "tier": user.tier,
        "subscription": None if not sub else {
            "id": sub.id, "tier": sub.tier, "currency": sub.currency,
            "amount": _minor_to_major(sub.amount_minor),
            "payment_method": sub.payment_method, "auto_renew": sub.auto_renew,
            "status": sub.status, "cancel_at_period_end": sub.cancel_at_period_end,
            "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
            "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        },
        "recent_payments": [{
            "reference": p.provider_reference, "amount": _minor_to_major(p.amount_minor),
            "currency": p.currency, "method": p.payment_method, "status": p.status,
            "created_at": p.created_at.isoformat(),
        } for p in payments],
    }


@router.post("/checkout")
async def create_checkout(payload: CheckoutInput, user: User = Depends(require_verified_user), db: Session = Depends(get_db)):
    if not _currency_enabled(payload.currency):
        raise HTTPException(status_code=503, detail=f"{payload.currency} checkout is not enabled for this merchant account yet. The developer must complete Flutterwave onboarding and enable this currency first.")
    amount_minor = _price(payload.tier, payload.currency)
    if amount_minor is None:
        raise HTTPException(status_code=409, detail=f"The {payload.tier.title()} price for {payload.currency} has not been configured yet. Contact support before purchasing.")
    if payload.payment_method not in _available_methods(payload.currency):
        raise HTTPException(status_code=422, detail=f"{payload.payment_method.replace('_', ' ').title()} is not currently enabled for {payload.currency} checkout.")
    if payload.currency == "USD" and payload.payment_method != "card":
        raise HTTPException(status_code=422, detail="USD checkout is configured for card payments only.")

    current = db.query(Subscription).filter_by(user_id=user.id, status="active").order_by(desc(Subscription.created_at)).first()
    if current and current.current_period_end and current.current_period_end > datetime.now(timezone.utc):
        if current.tier == payload.tier:
            raise HTTPException(status_code=409, detail="You already have an active subscription for this plan.")
        raise HTTPException(status_code=409, detail="You already have an active paid subscription. Cancel or allow it to expire before switching tiers, to avoid overlapping renewals.")

    _ensure_gateway_ready()
    tx_ref = "PSAI-" + uuid.uuid4().hex.upper()
    recurring = payload.payment_method == "card"
    provider_plan_id = None
    payment_options = "card"
    if payload.payment_method == "mobile_money":
        payment_options = "mobilemoneyghana"
    elif payload.payment_method == "bank_transfer":
        payment_options = "banktransfer"

    plan = None
    try:
        if recurring:
            plan = await _get_or_create_plan(db, payload.tier, payload.currency, amount_minor)
            provider_plan_id = plan.provider_plan_id
            payment_options = "card"  # Flutterwave payment-plan checkout is card-only.

        subscription = Subscription(
            id=str(uuid.uuid4()), user_id=user.id, provider="flutterwave",
            provider_plan_id=provider_plan_id, tier=payload.tier,
            currency=payload.currency, amount_minor=amount_minor,
            payment_method=payload.payment_method, auto_renew=recurring,
            status="pending",
        )
        payment = Payment(
            id=str(uuid.uuid4()), user_id=user.id, subscription_id=subscription.id,
            provider="flutterwave", provider_reference=tx_ref,
            amount_minor=amount_minor, currency=payload.currency,
            payment_method=payload.payment_method, status="pending",
        )
        db.add(subscription)
        db.add(payment)
        db.commit()

        provider_payload: dict[str, Any] = {
            "tx_ref": tx_ref,
            "amount": float(_minor_to_major(amount_minor)),
            "currency": payload.currency,
            "redirect_url": settings.frontend_url.rstrip("/") + "/?payment=return",
            "customer": {"email": user.email, "phonenumber": user.phone, "name": user.email.split("@")[0]},
            "customizations": {"title": "PlanSphere AI", "description": f"{payload.tier.title()} monthly subscription", "logo": ""},
            "payment_options": payment_options,
            "meta": {"plansphere_user_id": user.id, "plansphere_subscription_id": subscription.id, "plansphere_tier": payload.tier, "plansphere_method": payload.payment_method},
        }
        if provider_plan_id:
            provider_payload["payment_plan"] = int(provider_plan_id)
        response = await _flw_request("POST", "/payments", payload=provider_payload)
        link = (response.get("data") or {}).get("link")
        if not link:
            raise RuntimeError("Payment provider did not return a checkout link")
        return {
            "checkout_url": link,
            "reference": tx_ref,
            "currency": payload.currency,
            "amount": _minor_to_major(amount_minor),
            "tier": payload.tier,
            "payment_method": payload.payment_method,
            "auto_renew": recurring,
            "message": "Card payments renew automatically each month when the provider payment plan is active." if recurring else "This payment method is a one-time monthly payment. You must renew manually before the paid period ends.",
        }
    except HTTPException:
        raise
    except Exception as exc:
        pending = db.query(Payment).filter_by(provider_reference=tx_ref).first()
        if pending:
            pending.status = "failed"
            db.commit()
        raise HTTPException(status_code=502, detail=f"Could not start payment checkout: {exc}")


@router.get("/verify")
async def verify_checkout(transaction_id: str, tx_ref: str, user: User = Depends(require_verified_user), db: Session = Depends(get_db)):
    payment = db.query(Payment).filter_by(provider_reference=tx_ref, user_id=user.id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment reference was not found for this account.")
    if payment.status == "paid":
        return {"verified": True, "tier": user.tier, "message": "Payment was already verified."}
    try:
        tx = await _verify_transaction(transaction_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not verify payment with provider: {exc}")
    if str(tx.get("id")) != str(transaction_id):
        raise HTTPException(status_code=400, detail="The provider transaction ID did not match.")
    result = await _activate_payment(db, payment, tx)
    return {"verified": True, **result, "message": "Payment verified and subscription activated."}


@router.post("/cancel")
async def cancel_subscription(user: User = Depends(require_verified_user), db: Session = Depends(get_db)):
    sub = db.query(Subscription).filter_by(user_id=user.id, status="active").order_by(desc(Subscription.created_at)).first()
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription was found.")
    if sub.auto_renew:
        if not sub.provider_subscription_id:
            # Provider subscription ID can be looked up by account email and the plan.
            sub.provider_subscription_id = await _find_provider_subscription_id(user, sub.provider_plan_id)
        if not sub.provider_subscription_id:
            raise HTTPException(status_code=409, detail="The payment provider subscription could not be located safely. Contact support to cancel automatic renewal; access has not been changed.")
        try:
            await _flw_request("PUT", f"/subscriptions/{sub.provider_subscription_id}/cancel", payload={})
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"The provider could not cancel automatic renewal: {exc}")
        sub.cancel_at_period_end = True
        sub.status = "active"
        db.commit()
        return {"cancelled": True, "access_until": sub.current_period_end.isoformat() if sub.current_period_end else None, "message": "Automatic renewal has been cancelled. Your paid access remains available until the current period ends."}
    sub.cancel_at_period_end = True
    db.commit()
    return {"cancelled": True, "access_until": sub.current_period_end.isoformat() if sub.current_period_end else None, "message": "This payment method does not auto-renew. Your access remains until the paid period ends; no further payment will be taken automatically."}


@router.post("/flutterwave/webhook")
async def flutterwave_webhook(request: Request, db: Session = Depends(get_db)):
    if not settings.flutterwave_webhook_hash:
        raise HTTPException(status_code=503, detail="Flutterwave webhook hash is not configured.")
    received_hash = request.headers.get("verif-hash", "")
    if not received_hash or not hmac.compare_digest(received_hash, settings.flutterwave_webhook_hash):
        raise HTTPException(status_code=401, detail="Invalid payment webhook signature.")
    body = await request.json()
    event = str(body.get("event", ""))
    data = body.get("data") or {}

    if event == "subscription.cancelled":
        email = ((data.get("customer") or {}).get("email") or "").lower()
        user = db.query(User).filter_by(email=email).first() if email else None
        if user:
            sub = db.query(Subscription).filter_by(user_id=user.id, status="active", auto_renew=True).order_by(desc(Subscription.created_at)).first()
            if sub:
                sub.cancel_at_period_end = True
                db.commit()
        return {"received": True}

    if event not in ("charge.completed", "charge.success"):
        return {"received": True, "ignored": event}

    transaction_id = data.get("id")
    tx_ref = data.get("tx_ref")
    if not transaction_id:
        return {"received": True, "ignored": "missing transaction id"}

    # New checkout payments use our generated tx_ref. Recurring renewals may use
    # provider-generated references, so those are matched by verified email,
    # amount and currency against an active card subscription.
    payment = db.query(Payment).filter_by(provider_reference=tx_ref).first() if tx_ref else None
    try:
        tx = await _verify_transaction(transaction_id)
    except Exception:
        # Acknowledge the webhook; the provider retries and the user can also
        # invoke the authenticated /verify route after redirect.
        return {"received": True, "verification_pending": True}

    if payment:
        await _activate_payment(db, payment, tx)
        return {"received": True, "processed": True}

    email = ((tx.get("customer") or {}).get("email") or (data.get("customer") or {}).get("email") or "").lower()
    user = db.query(User).filter_by(email=email).first() if email else None
    if not user or str(tx.get("status", "")).lower() != "successful":
        return {"received": True, "ignored": "no matching active subscriber"}
    sub = db.query(Subscription).filter_by(user_id=user.id, status="active", auto_renew=True).order_by(desc(Subscription.created_at)).first()
    if not sub or str(tx.get("currency", "")).upper() != sub.currency:
        return {"received": True, "ignored": "subscription mismatch"}
    try:
        paid_minor = int(Decimal(str(tx.get("charged_amount", tx.get("amount")))) * 100)
    except InvalidOperation:
        return {"received": True, "ignored": "invalid renewal amount"}
    if paid_minor < sub.amount_minor:
        return {"received": True, "ignored": "underpaid renewal"}
    recurring_ref = str(tx_ref or f"FLW-{transaction_id}")
    if db.query(Payment).filter_by(provider_reference=recurring_ref).first():
        return {"received": True, "duplicate": True}
    db.add(Payment(
        id=str(uuid.uuid4()), user_id=user.id, subscription_id=sub.id,
        provider="flutterwave", provider_reference=recurring_ref,
        provider_transaction_id=str(transaction_id), amount_minor=paid_minor,
        currency=sub.currency, payment_method="card", status="paid",
        paid_at=datetime.now(timezone.utc),
    ))
    base = sub.current_period_end or datetime.now(timezone.utc)
    sub.current_period_start = base
    sub.current_period_end = _month_after(base)
    user.tier = sub.tier
    db.commit()
    return {"received": True, "renewal_processed": True}
