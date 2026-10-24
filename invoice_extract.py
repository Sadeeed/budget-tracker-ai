"""Invoice preparation/routing. Every result still requires explicit human review.

No OCR engine is required, no raw uploads are persisted, and this module never
creates transactions. Unverified image capability is an honest manual fallback.
"""

from dataclasses import dataclass
from typing import Literal, Optional

import pymupdf

from ai_service import AIService
from dnotifier_adapter import DNotifierError, InvalidAIOutput


MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 4


@dataclass(frozen=True)
class InvoiceProcessingResult:
    processing_path: Literal["digital text", "DNotifier image", "local OCR fallback", "manual"]
    draft: Optional[dict] = None
    failure: Optional[str] = None
    attempted_path: Optional[str] = None
    requires_review: bool = True


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract digital text only; a blank result is not OCR success."""
    try:
        with _open_pdf(pdf_bytes) as document:
            text = "\n".join(page.get_text() for page in document)
            if len(text) > 16000:
                raise InvalidAIOutput("PDF text exceeds the demo size limit. Use manual review.")
            return text.strip()
    except DNotifierError:
        raise
    except Exception:
        raise InvalidAIOutput("PDF could not be read. Use an unencrypted PDF or manual review.") from None


def _open_pdf(pdf_bytes: bytes):
    if not isinstance(pdf_bytes, bytes) or not 0 < len(pdf_bytes) <= MAX_BYTES:
        raise InvalidAIOutput("PDF must be nonempty and at most 10 MiB.")
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    if document.needs_pass or not 0 < len(document) <= MAX_PAGES:
        document.close()
        raise InvalidAIOutput("Use an unencrypted receipt PDF with 1–4 pages.")
    return document


def render_scanned_page(pdf_bytes: bytes) -> bytes:
    """Bound raster size. Multi-page scans are manual rather than guessed totals."""
    try:
        with _open_pdf(pdf_bytes) as document:
            if len(document) != 1:
                raise InvalidAIOutput("Multi-page scanned/mixed PDFs require manual review in this MVP.")
            page = document[0]
            if page.rect.width <= 0 or page.rect.height <= 0:
                raise InvalidAIOutput("PDF page has invalid dimensions.")
            scale = min(2.0, 1600 / page.rect.width, 1600 / page.rect.height)
            image = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            return image.tobytes("png")
    except DNotifierError:
        raise
    except Exception:
        raise InvalidAIOutput("Scanned PDF could not be rendered. Use manual review.") from None


async def process_invoice(file_bytes: bytes, mime_type: str,
                          ai_service: AIService) -> InvoiceProcessingResult:
    attempted = None
    try:
        if not isinstance(file_bytes, bytes) or not 0 < len(file_bytes) <= MAX_BYTES:
            raise InvalidAIOutput("Upload must be nonempty and at most 10 MiB.")
        if mime_type == "application/pdf":
            text = extract_pdf_text(file_bytes)
            # Avoid silently parsing only the digital pages of a mixed PDF.
            with _open_pdf(file_bytes) as document:
                all_digital = all(page.get_text().strip() for page in document)
            if text and all_digital:
                attempted = "digital text"
                draft = await ai_service.extract_invoice(text)
                return InvoiceProcessingResult("digital text", draft=draft, attempted_path=attempted)
            attempted = "DNotifier image"
            file_bytes = render_scanned_page(file_bytes)
            mime_type = "image/png"
        elif mime_type not in {"image/png", "image/jpeg"}:
            raise InvalidAIOutput("Supported uploads are PDF, PNG, and JPEG.")
        attempted = "DNotifier image"
        draft = await ai_service.extract_invoice_image(file_bytes, mime_type)
        # Null critical fields are not sufficient image extraction. Do not label
        # the fallback successful image processing or substitute guessed values.
        if any(draft.get(key) is None for key in ("merchant", "date", "total", "currency")):
            return InvoiceProcessingResult(
                "manual", draft=draft, attempted_path=attempted,
                failure="Image interpretation left required fields missing. Complete them manually.")
        return InvoiceProcessingResult("DNotifier image", draft=draft, attempted_path=attempted)
    except DNotifierError as exc:
        return InvoiceProcessingResult("manual", failure=str(exc), attempted_path=attempted)
    except Exception:
        # Unexpected library/adapter errors must not leak uploaded content or
        # make review unavailable. No values are committed on this path.
        return InvoiceProcessingResult(
            "manual", failure="Invoice processing failed. Retry or enter fields manually.",
            attempted_path=attempted)


async def verify_invoice_interpretation(service: AIService) -> dict:
    """Explicit opt-in service proof using only in-memory synthetic documents.

    Three real AI requests when called with the real service. Never called by
    the app/default tests; creates no invoice or expense records.
    """
    text = (
        "SYNTHETIC DEMO RECEIPT\nMerchant: Synthetic Grocery Market\n"
        "Invoice number: DEMO-1042\nDate: 2026-10-12\nCurrency: PKR\n"
        "Items: rice, milk, bread\nSubtotal: 4500.00\nTax: 350.00\n"
        "GRAND TOTAL: PKR 4850.00\n"
    )
    expected = {"merchant": "Synthetic Grocery Market", "invoice_number": "DEMO-1042",
                "date": "2026-10-12", "total": "4850.00", "currency": "PKR",
                "suggested_category": "Groceries"}

    def matches(draft):
        return isinstance(draft, dict) and all(draft.get(key) == value for key, value in expected.items())

    direct_draft = await service.extract_invoice(text)
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), text)
        pdf_bytes = document.tobytes()
    pdf_result = await process_invoice(pdf_bytes, "application/pdf", service)
    incomplete_draft = await service.extract_invoice(
        "SYNTHETIC RECEIPT\nMerchant: Synthetic Grocery Market\n"
        "Items: rice, milk, bread\nCurrency: PKR\nGRAND TOTAL: PKR 4850.00\n"
        "Date is illegible. Invoice number is not visible."
    )
    checks = {
        "direct_text_matches_source": matches(direct_draft),
        "digital_pdf_matches_source": matches(pdf_result.draft),
        "digital_pdf_path_verified": pdf_result.processing_path == "digital text" and pdf_result.failure is None,
        "missing_date_and_invoice_number_preserved": (
            incomplete_draft.get("date") is None and incomplete_draft.get("invoice_number") is None
            and {"date", "invoice_number"} <= set(incomplete_draft.get("missing_fields", []))
        ),
        "pdf_requires_review": pdf_result.requires_review,
    }
    return {"verified": all(checks.values()), "synthetic": True, "expense_committed": False,
            "checks": checks, "direct_draft": direct_draft, "pdf_draft": pdf_result.draft,
            "incomplete_draft": incomplete_draft}


def main() -> int:
    import argparse
    import json

    from dnotifier_adapter import DNotifierAdapter, run_async

    parser = argparse.ArgumentParser(description="Live synthetic invoice text/PDF interpretation verification.")
    parser.add_argument("--smoke-invoice", action="store_true", required=True,
                        help="Opt in to three real AI requests; synthetic data only, no DB writes.")
    parser.parse_args()
    try:
        result = run_async(verify_invoice_interpretation, AIService(DNotifierAdapter.from_environment()))
    except DNotifierError as exc:
        print(json.dumps({"verified": False, "reason": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
