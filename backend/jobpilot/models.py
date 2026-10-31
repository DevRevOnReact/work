import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


def uid() -> str:
    return str(uuid.uuid4())


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(default=1)


class Resume(Base):
    __tablename__ = "resumes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    variant: Mapped[str] = mapped_column(String(80))
    filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("provider", "board"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String(40))
    board: Mapped[str] = mapped_column(String(120))
    company: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    provider: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(255))
    canonical_url: Mapped[str] = mapped_column(Text, unique=True)
    company: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    location: Mapped[str] = mapped_column(String(400))
    description: Mapped[str] = mapped_column(Text)
    salary: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    eligibility: Mapped[str] = mapped_column(String(20), default="unknown")
    explanation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duplicate_of: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    state: Mapped[str] = mapped_column(String(40), default="DISCOVERED", index=True)
    resume_id: Mapped[str | None] = mapped_column(ForeignKey("resumes.id"))
    answers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    attention: Mapped[str | None] = mapped_column(Text)
    attempt_id: Mapped[str | None] = mapped_column(String(36))
    submit_intent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id"), index=True)
    kind: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Answer(Base):
    __tablename__ = "answers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    question: Mapped[str] = mapped_column(Text, unique=True)
    answer: Mapped[str] = mapped_column(Text)


class Control(Base):
    __tablename__ = "control"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_apply: Mapped[bool] = mapped_column(Boolean, default=False)
    min_score: Mapped[int] = mapped_column(default=80)
    daily_limit: Mapped[int] = mapped_column(default=20)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    message: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    next_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class SavedSearch(Base):
    __tablename__ = "saved_searches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    resume_id: Mapped[str] = mapped_column(ForeignKey("resumes.id"))
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="idle")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[str]] = mapped_column(JSON, default=list)
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CompanyDiscovery(Base):
    __tablename__ = "company_discoveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    country: Mapped[str] = mapped_column(String(80))
    page: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class ReferralLead(Base):
    __tablename__ = "referral_leads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(200), default="")
    profile_url: Mapped[str] = mapped_column(Text, unique=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200), unique=True)
    careers_url: Mapped[str] = mapped_column(Text, default="")
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ResumeFile(Base):
    __tablename__ = "resume_files"
    resume_id: Mapped[str] = mapped_column(ForeignKey("resumes.id"), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)


class SearchNotification(Base):
    __tablename__ = "search_notifications"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    search_id: Mapped[str] = mapped_column(ForeignKey("saved_searches.id"))
    channel: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    error: Mapped[str | None] = mapped_column(Text)
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NotificationSeen(Base):
    __tablename__ = "notification_seen"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)


class TopVacancy(Base):
    __tablename__ = "top_vacancies"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    resume_id: Mapped[str] = mapped_column(String(36), index=True)
    search_id: Mapped[str] = mapped_column(String(36))
    resume_filename: Mapped[str] = mapped_column(String(255))
    search_name: Mapped[str] = mapped_column(String(120))
    score: Mapped[int] = mapped_column(Integer, index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LinkedInCandidate(Base):
    __tablename__ = "linkedin_candidates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
