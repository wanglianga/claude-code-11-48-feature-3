"""ORM models for the controlled multi-center analysis platform."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32))  # data_admin | researcher | reviewer
    display_name: Mapped[str] = mapped_column(String(128))


class Center(Base):
    __tablename__ = "centers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)  # C1..C6
    name: Mapped[str] = mapped_column(String(128))
    agent_url: Mapped[str] = mapped_column(String(256))


class Dictionary(Base):
    __tablename__ = "dictionaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    variables: Mapped[list["Variable"]] = relationship(back_populates="dictionary")


class Variable(Base):
    __tablename__ = "variables"
    __table_args__ = (UniqueConstraint("dict_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dict_id: Mapped[int] = mapped_column(ForeignKey("dictionaries.id"))
    name: Mapped[str] = mapped_column(String(64))  # e.g. followup.outcome
    entity: Mapped[str] = mapped_column(String(32))  # patient|diagnosis|treatment|followup
    label: Mapped[str] = mapped_column(String(128))
    value_type: Mapped[str] = mapped_column(String(16))  # categorical|numeric|date
    allowed_values: Mapped[list | None] = mapped_column(JSON, nullable=True)
    dictionary: Mapped[Dictionary] = relationship(back_populates="variables")


class CenterMapping(Base):
    """Maps a canonical dictionary variable to a center-local CSV field."""

    __tablename__ = "center_mappings"
    __table_args__ = (UniqueConstraint("center_id", "dict_id", "variable_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    dict_id: Mapped[int] = mapped_column(ForeignKey("dictionaries.id"))
    variable_name: Mapped[str] = mapped_column(String(64))
    local_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value_map: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="mapped")  # mapped|missing


class DataBatch(Base):
    """A data batch registered by a center; locked batches are immutable run inputs."""

    __tablename__ = "data_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    label: Mapped[str] = mapped_column(String(64))  # e.g. 2026Q3
    dict_id: Mapped[int] = mapped_column(ForeignKey("dictionaries.id"))
    status: Mapped[str] = mapped_column(String(16), default="registered")
    # registered -> locked -> superseded (when a newer batch is locked)
    record_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    available_scope: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    center: Mapped[Center] = relationship()


class CohortRule(Base):
    __tablename__ = "cohort_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RuleVersion(Base):
    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("cohort_rules.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    # draft -> in_review -> approved | rejected ; a newer draft supersedes for editing
    status: Mapped[str] = mapped_column(String(16), default="draft")
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    conditions_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule: Mapped[CohortRule] = relationship()


class Run(Base):
    """One orchestrated multi-center execution of an approved rule version."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_version_id: Mapped[int] = mapped_column(ForeignKey("rule_versions.id"))
    request_spec: Mapped[dict] = mapped_column(JSON)
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # pending -> running -> completed | completed_with_gaps | failed
    k_threshold: Mapped[int] = mapped_column(Integer, default=5)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rule_version: Mapped[RuleVersion] = relationship()
    center_results: Mapped[list["RunCenterResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    batches: Mapped[list["RunBatch"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RunBatch(Base):
    """Immutable snapshot: which locked batch each center contributed to a run."""

    __tablename__ = "run_batches"
    __table_args__ = (UniqueConstraint("run_id", "center_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    batch_id: Mapped[int] = mapped_column(ForeignKey("data_batches.id"))
    batch_label: Mapped[str] = mapped_column(String(64))
    run: Mapped[Run] = relationship(back_populates="batches")
    batch: Mapped[DataBatch] = relationship()


class RunCenterResult(Base):
    __tablename__ = "run_center_results"
    __table_args__ = (UniqueConstraint("run_id", "center_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # pending | success | failed | missing_mapping
    aggregates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run: Mapped[Run] = relationship(back_populates="center_results")
    center: Mapped[Center] = relationship()


class OutputTable(Base):
    """An aggregated result table a researcher wants to export; needs review."""

    __tablename__ = "output_tables"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    title: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(24), default="pending_review")
    # pending_review | approved | rejected
    table_data: Mapped[dict] = mapped_column(JSON)  # groups + totals + coverage
    flags: Mapped[list] = mapped_column(JSON, default=list)  # small-cell findings
    k_threshold: Mapped[int] = mapped_column(Integer, default=5)
    run: Mapped[Run] = relationship()


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("output_tables.id"))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(16))  # approve | suppress | merge | reject
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    output: Mapped[OutputTable] = relationship()


class PublishedReport(Base):
    """Approved output frozen with full provenance for reproducibility."""

    __tablename__ = "published_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("output_tables.id"), unique=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    rule_version_id: Mapped[int] = mapped_column(ForeignKey("rule_versions.id"))
    decision_id: Mapped[int] = mapped_column(ForeignKey("review_decisions.id"))
    published_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    report: Mapped[dict] = mapped_column(JSON)  # frozen table + provenance + hashes
    report_hash: Mapped[str] = mapped_column(String(64))
    output: Mapped[OutputTable] = relationship()


# ---------------------------------------------------------------------------
# Cross-center effect aggregation (research protocol -> effect run -> review)
# ---------------------------------------------------------------------------


class EffectPlan(Base):
    """A research protocol for cross-center effect aggregation."""

    __tablename__ = "effect_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class EffectPlanVersion(Base):
    """One versioned protocol spec; methodology approval happens per version."""

    __tablename__ = "effect_plan_versions"
    __table_args__ = (UniqueConstraint("plan_id", "version_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("effect_plans.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    # draft -> in_review -> approved | rejected
    status: Mapped[str] = mapped_column(String(16), default="draft")
    # spec: {cohort_conditions, treatment_variable, treated, control,
    #        outcome_variable, outcome_value, covariates, covariate_types,
    #        measures, primary_measure, primary_pooling}
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    spec_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan: Mapped[EffectPlan] = relationship()


class EffectRun(Base):
    """One coordinated effect-aggregation run of an approved plan version.

    Linked by its id to center-local diagnostics, the pooled coordination
    result, disclosure decisions and the published report. A corrected center
    batch can only produce a NEW run (new pool_version); old runs keep their
    center set and conclusions."""

    __tablename__ = "effect_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_version_id: Mapped[int] = mapped_column(ForeignKey("effect_plan_versions.id"))
    pool_version: Mapped[int] = mapped_column(Integer)  # per-plan sequence
    spec_snapshot: Mapped[dict] = mapped_column(JSON)  # frozen plan spec at start
    spec_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # pending -> running -> completed | completed_with_gaps | failed
    k_threshold: Mapped[int] = mapped_column(Integer, default=5)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plan_version: Mapped[EffectPlanVersion] = relationship()
    center_results: Mapped[list["EffectRunCenter"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    batches: Mapped[list["EffectRunBatch"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class EffectRunBatch(Base):
    """Immutable snapshot: which locked batch each center contributed."""

    __tablename__ = "effect_run_batches"
    __table_args__ = (UniqueConstraint("run_id", "center_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("effect_runs.id"))
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    batch_id: Mapped[int] = mapped_column(ForeignKey("data_batches.id"))
    batch_label: Mapped[str] = mapped_column(String(64))
    run: Mapped[EffectRun] = relationship(back_populates="batches")
    batch: Mapped[DataBatch] = relationship()


class EffectRunCenter(Base):
    """Per-center local-model outcome for an effect run.

    status: pending | included | insufficient_events | small_cell_suppressed
          | no_common_support | model_not_converged | missing_mapping | failed
    diagnostics is populated ONLY for included centers (passed min-cell)."""

    __tablename__ = "effect_run_centers"
    __table_args__ = (UniqueConstraint("run_id", "center_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("effect_runs.id"))
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    diagnostics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # protected info
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run: Mapped[EffectRun] = relationship(back_populates="center_results")
    center: Mapped[Center] = relationship()


class EffectOutput(Base):
    """A pooled effect result submitted for methodology/disclosure review."""

    __tablename__ = "effect_outputs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("effect_runs.id"))
    title: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(24), default="pending_review")
    # pending_review | approved | rejected
    # payload: {pooling, centers, protected, suppressed_centers, pooled_over,
    #           coverage, premises, support_range, pooled_suppressed}
    payload: Mapped[dict] = mapped_column(JSON)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    k_threshold: Mapped[int] = mapped_column(Integer, default=5)
    run: Mapped[EffectRun] = relationship()


class EffectReviewDecision(Base):
    __tablename__ = "effect_review_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("effect_outputs.id"))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # suppress_center | repool | suppress_pooled | approve | reject
    action: Mapped[str] = mapped_column(String(24))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    output: Mapped[EffectOutput] = relationship()


class PublishedEffectReport(Base):
    """Approved effect output frozen with full provenance and content hash."""

    __tablename__ = "published_effect_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("effect_outputs.id"), unique=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("effect_runs.id"))
    plan_version_id: Mapped[int] = mapped_column(ForeignKey("effect_plan_versions.id"))
    decision_id: Mapped[int] = mapped_column(ForeignKey("effect_review_decisions.id"))
    published_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    report: Mapped[dict] = mapped_column(JSON)
    report_hash: Mapped[str] = mapped_column(String(64))
    output: Mapped[EffectOutput] = relationship()
