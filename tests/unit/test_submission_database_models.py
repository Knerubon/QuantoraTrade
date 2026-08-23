"""Schema and stable hashing checks for durable submission persistence."""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from quantora_trade.infrastructure.database.execution_models import (
    DecisionEvidenceModel,
    RiskAssessmentEvidenceModel,
    SubmissionJournalModel,
)


def test_execution_tables_have_expected_identity_and_constraints() -> None:
    assert set(DecisionEvidenceModel.__table__.primary_key.columns.keys()) == {"id"}
    assert set(RiskAssessmentEvidenceModel.__table__.primary_key.columns.keys()) == {"id"}
    assert set(SubmissionJournalModel.__table__.primary_key.columns.keys()) == {"idempotency_key"}
    names = {constraint.name for constraint in SubmissionJournalModel.__table__.constraints}
    assert "ck_submission_journal_state_valid" in names
    assert "ck_submission_journal_request_hash_valid" in names
    assert "ck_submission_journal_completed_result_valid" in names
    ddl = str(CreateTable(SubmissionJournalModel.__table__).compile(dialect=postgresql.dialect()))
    assert "request_hash ~ '^[0-9a-f]{64}$'" in ddl
    assert "claim_owner UUID NOT NULL" in ddl
