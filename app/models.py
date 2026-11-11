from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from .database import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True)
    username = Column(String(32), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    phone = Column(String(32), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    email_verified = Column(Boolean, default=False, nullable=False)
    phone_verified = Column(Boolean, default=False, nullable=False)
    tier = Column(String(24), default="professional", nullable=False)
    monthly_messages = Column(Integer, default=0, nullable=False)
    usage_month = Column(String(7), default="", nullable=False)
    terms_accepted_at = Column(DateTime(timezone=True), nullable=True)
    terms_version = Column(String(40), nullable=True)
    privacy_acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    privacy_version = Column(String(40), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    documents = relationship("Document", back_populates="owner", cascade="all, delete-orphan")


class VerificationCode(Base):
    __tablename__ = "verification_codes"
    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    channel = Column(String(12), nullable=False)
    code_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    consumed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class Document(Base):
    __tablename__ = "documents"
    id = Column(String(36), primary_key=True)
    owner_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    file_type = Column(String(12), nullable=False)
    storage_path = Column(Text, nullable=False)
    extracted_text = Column(Text, default="", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    owner = relationship("User", back_populates="documents")


class GatewayPlan(Base):
    __tablename__ = "gateway_plans"
    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(30), nullable=False, default="flutterwave")
    tier = Column(String(24), nullable=False)
    currency = Column(String(3), nullable=False)
    amount_minor = Column(Integer, nullable=False)
    interval = Column(String(20), nullable=False, default="monthly")
    provider_plan_id = Column(String(80), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("provider", "tier", "currency", "amount_minor", name="uq_gateway_plan_key"),)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(30), nullable=False, default="flutterwave")
    provider_subscription_id = Column(String(80), nullable=True, index=True)
    provider_plan_id = Column(String(80), nullable=True)
    tier = Column(String(24), nullable=False)
    currency = Column(String(3), nullable=False)
    amount_minor = Column(Integer, nullable=False)
    payment_method = Column(String(24), nullable=False)
    auto_renew = Column(Boolean, default=False, nullable=False)
    status = Column(String(24), default="pending", nullable=False, index=True)
    cancel_at_period_end = Column(Boolean, default=False, nullable=False)
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Payment(Base):
    __tablename__ = "payments"
    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_id = Column(String(36), ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True, index=True)
    provider = Column(String(30), nullable=False, default="flutterwave")
    provider_reference = Column(String(120), nullable=False, unique=True, index=True)
    provider_transaction_id = Column(String(80), nullable=True, index=True)
    amount_minor = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False)
    payment_method = Column(String(24), nullable=False)
    status = Column(String(24), default="pending", nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)
