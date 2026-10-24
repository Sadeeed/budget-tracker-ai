"""Validated interpretation only: no database writes or authoritative finance math.

Private Pydantic contracts mirror DESIGN.md pending Dev 1's shared models.
Keep field/signature changes coordinated with that owner.
"""

from datetime import date
from decimal import Decimal
import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from dnotifier_adapter import DNotifierAdapter, InvalidAIOutput
from prompts import CATEGORIES, analysis_prompt, invoice_prompt


Category = Literal[
    "Housing", "Groceries", "Dining", "Transport", "Utilities", "Shopping",
    "Health", "Entertainment", "Subscriptions", "Education", "Other",
]
Priority = Literal["high", "medium", "low"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class InvoiceDraft(Contract):
    merchant: Optional[str] = Field(max_length=160)
    invoice_number: Optional[str] = Field(max_length=100)
    date: Optional[str]
    total: Optional[str]
    currency: Optional[Literal["PKR"]]
    suggested_category: Category
    confidence: Priority
    missing_fields: list[str] = Field(max_length=10)
    evidence_snippet: str = Field(max_length=240)

    @field_validator("date")
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("ISO date required")
            date.fromisoformat(value)
        return value

    @field_validator("total")
    @classmethod
    def valid_total(cls, value):
        if value is not None and (not re.fullmatch(r"\d{1,12}(?:\.\d{1,2})?", value)
                                  or not Decimal(value).is_finite()):
            raise ValueError("Nonnegative decimal string required")
        return value


class Finding(Contract):
    title: str = Field(min_length=1, max_length=160)
    severity: Priority
    evidence: str = Field(min_length=1, max_length=600)
    category: Optional[Category]


class Recommendation(Contract):
    action: str = Field(min_length=1, max_length=400)
    reason: str = Field(min_length=1, max_length=400)
    category: Optional[Category]
    priority: Priority


class MonthlyReport(Contract):
    summary: str = Field(min_length=1, max_length=1200)
    findings: list[Finding] = Field(max_length=3)
    recommendations: list[Recommendation] = Field(max_length=3)
    limitations: list[str] = Field(max_length=10)


class CategoryStats(Contract):
    category: Category
    spent_minor: int
    budget_minor: Optional[int] = Field(default=None, ge=0)
    over_budget_minor: Optional[int] = None
    percent_used: Optional[float] = None
    previous_month_spent_minor: Optional[int] = None
    mom_change_minor: Optional[int] = None
    mom_change_pct: Optional[float] = None

    @model_validator(mode="after")
    def missing_data_is_not_a_comparison(self):
        if self.previous_month_spent_minor is None and (
            self.mom_change_minor is not None or self.mom_change_pct is not None
        ):
            raise ValueError("History is required for month-over-month figures")
        if self.previous_month_spent_minor == 0 and self.mom_change_pct is not None:
            raise ValueError("Zero prior spending has no percentage change")
        if self.budget_minor is None and (self.over_budget_minor is not None or self.percent_used is not None):
            raise ValueError("A limit is required for budget comparisons")
        return self


class ExpenseFact(Contract):
    description: str = Field(max_length=160)
    amount_minor: int = Field(ge=0)
    date: str
    category: Optional[Category] = None


class MonthlyStats(Contract):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    currency: Literal["PKR"] = "PKR"
    overall_budget_minor: Optional[int] = Field(default=None, ge=0)
    net_expenses_minor: int
    income_minor: int = Field(ge=0)
    overall_variance_minor: Optional[int] = None
    overall_percent_used: Optional[float] = None
    categories: list[CategoryStats] = Field(max_length=len(CATEGORIES))
    largest_expenses: list[ExpenseFact] = Field(default_factory=list, max_length=10)
    data_notes: list[str] = Field(default_factory=list, max_length=10)


def sanitize_text(text: str) -> str:
    """Best-effort redaction, not a production PII detector. Never log the input."""
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted email]", text)
    text = re.sub(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b", "[redacted account]", text, flags=re.I)
    text = re.sub(r"(?<!\d)(?:\d[ -]?){12,19}(?!\d)", "[redacted number]", text)
    # Remove entire labelled personal-information lines, retaining financial facts.
    text = re.sub(
        r"(?im)^\s*(?:customer(?: name)?|bill(?:ed)? to|ship(?:ping)? to|address|phone|mobile|account|card)\s*:.*$",
        "[redacted personal details]", text)
    return text


class AIService:
    def __init__(self, adapter: DNotifierAdapter):
        self.adapter = adapter

    async def extract_invoice(self, extracted_text: str) -> dict:
        if not isinstance(extracted_text, str) or not extracted_text.strip():
            raise InvalidAIOutput("No invoice text was extracted. Enter fields manually.")
        if len(extracted_text) > 16000:
            raise InvalidAIOutput("Invoice text is too large. Use a shorter receipt or manual review.")
        result = await self.adapter.request_json(invoice_prompt(sanitize_text(extracted_text)))
        return self._invoice(result)

    async def extract_invoice_image(self, image_bytes: bytes, mime_type: str) -> dict:
        if mime_type not in {"image/png", "image/jpeg"}:
            raise InvalidAIOutput("Use a PNG or JPEG receipt image.")
        if not isinstance(image_bytes, bytes) or not 0 < len(image_bytes) <= 10 * 1024 * 1024:
            raise InvalidAIOutput("Receipt image must be nonempty and at most 10 MiB.")
        return self._invoice(await self.adapter.extract_invoice_image(image_bytes, mime_type))

    @staticmethod
    def _invoice(result: dict) -> dict:
        try:
            draft = InvoiceDraft.model_validate(result).model_dump()
        except ValidationError:
            # Pydantic's raw exception includes input data: do not expose it.
            raise InvalidAIOutput("Invoice AI output failed validation. Retry or enter fields manually.") from None
        missing = set(draft["missing_fields"])
        for key in ("merchant", "date", "total", "currency", "invoice_number"):
            if draft[key] is None or (isinstance(draft[key], str) and not draft[key].strip()):
                draft[key] = None
                missing.add(key)
        draft["missing_fields"] = sorted(missing)
        return draft

    async def analyze_month(self, computed_stats: dict) -> dict:
        try:
            stats = MonthlyStats.model_validate(computed_stats).model_dump()
        except ValidationError:
            raise InvalidAIOutput("Monthly statistics do not match the agreed aggregate contract.") from None
        for fact in stats["largest_expenses"]:
            fact["description"] = sanitize_text(fact["description"])
        stats["data_notes"] = [sanitize_text(note) for note in stats["data_notes"]]
        result = await self.adapter.request_json(analysis_prompt(stats))
        try:
            report = MonthlyReport.model_validate(result).model_dump()
        except ValidationError:
            raise InvalidAIOutput("Monthly AI output failed validation. Retry analysis; no report was saved.") from None
        allowed_money = set()

        def collect_money(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.endswith("_minor") and type(item) is int:
                        allowed_money.add(Decimal(item) / Decimal(100))
                    else:
                        collect_money(item)
            elif isinstance(value, list):
                for item in value:
                    collect_money(item)

        collect_money(stats)
        import json
        for amount in re.findall(r"\bRs\.?\s*([+-]?\d[\d,]*(?:\.\d+)?)", json.dumps(report)):
            if Decimal(amount.replace(",", "")) not in allowed_money:
                raise InvalidAIOutput("AI cited an unsupported rupee amount. Retry analysis; no report was saved.")
        categories = {row["category"] for row in stats["categories"]}
        for item in report["findings"] + report["recommendations"]:
            if item["category"] is not None and item["category"] not in categories:
                raise InvalidAIOutput("AI cited a category absent from the statistics. Retry analysis.")
        has_history = any(row["previous_month_spent_minor"] is not None for row in stats["categories"])
        history_categories = {row["category"] for row in stats["categories"]
                              if row["previous_month_spent_minor"] is not None}
        budgeted_categories = {row["category"] for row in stats["categories"] if row["budget_minor"] is not None}
        overrun_words = re.compile(r"\b(?:over[- ]budget|overspend\w*|overrun\w*|exceed\w* (?:its |the |your )?(?:budget|limit))", re.I)
        comparison_words = re.compile(r"\b(?:month[- ]over[- ]month|mom|(?:previous|last|prior) month)\b", re.I)
        if not has_history and comparison_words.search(report["summary"]):
            raise InvalidAIOutput("AI cited a month-over-month comparison without history. Retry analysis.")
        if not budgeted_categories and stats["overall_budget_minor"] is None and overrun_words.search(report["summary"]):
            raise InvalidAIOutput("AI claimed an overrun without a configured budget. Retry analysis.")
        for finding in report["findings"]:
            text = finding["title"] + " " + finding["evidence"]
            category = finding["category"]
            comparison_available = has_history if category is None else category in history_categories
            if not comparison_available and comparison_words.search(text):
                raise InvalidAIOutput("AI cited a month-over-month comparison without history. Retry analysis.")
            if overrun_words.search(text):
                if (category is not None and category not in budgeted_categories) or (
                    category is None and stats["overall_budget_minor"] is None
                ):
                    raise InvalidAIOutput("AI claimed an overrun without a configured budget. Retry analysis.")
        if not has_history:
            # A deterministic UI limitation is retained even if the model omits it.
            report["limitations"] = list(dict.fromkeys(report["limitations"] + [
                "Previous-month data is unavailable; no month-over-month comparison is supported."]))
        if stats["overall_budget_minor"] is None and all(row["budget_minor"] is None for row in stats["categories"]):
            report["limitations"] = list(dict.fromkeys(report["limitations"] + [
                "No budget limits are configured; overspending cannot be assessed."]))
        return report


def main() -> int:
    """Explicit live service probe; never part of the default test suite."""
    import argparse
    import json

    from dnotifier_adapter import DNotifierError, run_async

    parser = argparse.ArgumentParser(description="Live monthly-analysis check using synthetic aggregates only.")
    parser.add_argument("--smoke-month", action="store_true", required=True,
                        help="Opt in to one real AI report request (may incur usage); no DB writes.")
    parser.parse_args()
    # Independent service fixture, not a claim that CSV import/finance integration
    # exists. All amounts are integer paisa; definitive totals are computed here.
    expenses = {"Dining": [70000, 50000], "Groceries": [30000, 50000]}
    budgets = {"Dining": 100000, "Groceries": 100000}
    categories = []
    for category, amounts in expenses.items():
        spent = sum(amounts)
        limit = budgets[category]
        categories.append({
            "category": category, "spent_minor": spent, "budget_minor": limit,
            "over_budget_minor": max(0, spent - limit),
            "percent_used": spent * 100 / limit,
        })
    overall_budget = sum(budgets.values())
    net_spent = sum(sum(amounts) for amounts in expenses.values())
    stats = {
        "month": "2026-10", "currency": "PKR", "overall_budget_minor": overall_budget,
        "net_expenses_minor": net_spent, "income_minor": 300000,
        "overall_variance_minor": net_spent - overall_budget,
        "categories": categories, "largest_expenses": [],
        "data_notes": ["Synthetic service fixture, not imported CSV data.",
                       "Totals calculated in Python; previous-month history is unavailable."],
    }
    try:
        service = AIService(DNotifierAdapter.from_environment())
        report = run_async(service.analyze_month, stats)
    except DNotifierError as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}))
        return 1
    print(json.dumps({"status": "validated_live_report", "synthetic": True,
                      "expense_committed": False, "input_statistics": stats,
                      "report": report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
