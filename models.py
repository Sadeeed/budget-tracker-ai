"""Shared data contracts and Pydantic validation schemas for BudgetPilot.

All models adhere to docs/DESIGN.md specifications and serve as the contract
boundary between Dev 1 (local app, storage, UI) and Dev 2 (AI service, extraction).
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from config import OVERALL_BUDGET_KEY
from prompts import MAX_FINDINGS_LIMIT


class TransactionType(str, Enum):
    EXPENSE = "expense"
    REFUND = "refund"
    INCOME = "income"
    TRANSFER = "transfer"


class TransactionSource(str, Enum):
    CSV = "csv"
    INVOICE = "invoice"
    MANUAL = "manual"


class PaymentStatus(str, Enum):
    PAID = "paid"
    UNPAID = "unpaid"


class AlertStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class ProcessingPath(str, Enum):
    DIGITAL_TEXT = "digital_text"
    DNOTIFIER_IMAGE = "dnotifier_image"
    LOCAL_OCR_FALLBACK = "local_ocr_fallback"
    MANUAL = "manual"


# --- Transaction & Budget Models ---

class TransactionCreate(BaseModel):
    occurred_on: str = Field(..., description="ISO Date string YYYY-MM-DD")
    description: str
    merchant: Optional[str] = None
    amount_minor: int = Field(..., ge=0, description="Amount in integer minor units (paisa)")
    category: str
    type: TransactionType = TransactionType.EXPENSE
    source: TransactionSource = TransactionSource.CSV
    import_id: Optional[int] = None
    source_row: Optional[int] = None
    invoice_id: Optional[int] = None


class TransactionRecord(TransactionCreate):
    id: int
    created_at: str


class BudgetRecord(BaseModel):
    id: Optional[int] = None
    month: str = Field(..., description="Calendar month YYYY-MM")
    category: str = Field(..., description="Category name or '__overall__'")
    limit_minor: int = Field(..., ge=0, description="Budget cap in minor units")


# --- Invoice Models ---

class InvoiceDraft(BaseModel):
    """Draft extraction contract returned by AIService or manual entry."""
    merchant: Optional[str] = None
    invoice_number: Optional[str] = None
    date: Optional[str] = None  # YYYY-MM-DD
    total: Optional[str] = None  # Decimal string (e.g. "4850.00")
    currency: Optional[str] = "PKR"
    suggested_category: Optional[str] = "Other"
    confidence: str = Field("medium", description="high | medium | low")
    missing_fields: List[str] = Field(default_factory=list)
    evidence_snippet: Optional[str] = None
    processing_path: ProcessingPath = ProcessingPath.MANUAL
    failure_reason: Optional[str] = None


class InvoiceRecord(BaseModel):
    """Persisted invoice record."""
    id: Optional[int] = None
    invoice_number: Optional[str] = None
    merchant: str
    invoice_date: str
    total_minor: int = Field(..., ge=0)
    category: str
    payment_status: PaymentStatus
    file_sha256: Optional[str] = None
    linked_transaction_id: Optional[int] = None
    created_at: Optional[str] = None


# --- Financial Statistics Models (AI Input Contract) ---

class CategoryMonthlyStats(BaseModel):
    category: str
    spent_minor: int
    budget_minor: Optional[int] = None
    over_budget_minor: Optional[int] = None
    percent_used: Optional[float] = None
    previous_month_spent_minor: Optional[int] = None
    mom_change_minor: Optional[int] = None
    mom_change_pct: Optional[float] = None


class TopExpenseItem(BaseModel):
    description: str
    amount_minor: int
    date: str
    category: str


class MonthlyComputedStats(BaseModel):
    """Authoritative financial statistics payload computed by Python.

    This is the input contract passed to AIService.analyze_month().
    """
    month: str  # YYYY-MM
    currency: str = "PKR"
    overall_budget_minor: Optional[int] = None
    net_expenses_minor: int
    income_minor: int
    overall_variance_minor: Optional[int] = None
    overall_percent_used: Optional[float] = None
    categories: List[CategoryMonthlyStats]
    largest_expenses: List[TopExpenseItem]
    data_notes: List[str] = Field(default_factory=list)


# --- Monthly AI Report Models (AI Output Contract) ---

class MonthlyAnalysisFinding(BaseModel):
    title: str
    severity: str = Field(..., description="high | medium | low | info")
    evidence: str
    category: Optional[str] = None


class MonthlyAnalysisRecommendation(BaseModel):
    action: str
    reason: str
    category: Optional[str] = None
    priority: str = Field("medium", description="high | medium | low")


class MonthlyAnalysisReport(BaseModel):
    """Validated output structure produced by the DNotifier AI monthly analyst."""
    summary: str
    findings: List[MonthlyAnalysisFinding] = Field(..., max_length=MAX_FINDINGS_LIMIT)
    recommendations: List[MonthlyAnalysisRecommendation] = Field(..., max_length=MAX_FINDINGS_LIMIT)
    limitations: List[str] = Field(default_factory=list)


# --- Alert & Report Persistence Models ---

class AlertRecord(BaseModel):
    id: Optional[int] = None
    month: str
    category: str
    threshold: int = Field(..., description="80 or 100")
    amount_minor: int
    status: AlertStatus = AlertStatus.PENDING
    created_at: Optional[str] = None
    sent_at: Optional[str] = None


class ReportRecord(BaseModel):
    id: Optional[int] = None
    month: str
    stats_json: str
    analysis_json: str
    transaction_revision: int
    created_at: str
