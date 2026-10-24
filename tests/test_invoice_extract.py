"""Build synthetic PDF fixtures in memory; do not alter Dev 1's sample_data."""

import asyncio

import pymupdf
import pytest

from dnotifier_adapter import CapabilityUnavailable, InvalidAIOutput
from invoice_extract import extract_pdf_text, process_invoice, render_scanned_page
from invoice_extract import verify_invoice_interpretation


def pdf(text="", pages=1):
    with pymupdf.open() as document:
        for _ in range(pages):
            page = document.new_page()
            if text:
                page.insert_text((72, 72), text)
        return document.tobytes()


class ServiceDouble:
    def __init__(self, image_failure=True, missing_total=False):
        self.calls = []
        self.image_failure = image_failure
        self.missing_total = missing_total

    async def extract_invoice(self, text):
        self.calls.append(("text", text))
        return {"merchant": "Synthetic Market", "date": "2026-10-12", "total": "10.00", "currency": "PKR"}

    async def extract_invoice_image(self, image, mime):
        self.calls.append(("image", mime, image[:8]))
        if self.image_failure:
            raise CapabilityUnavailable("Image support unverified. Use manual review.")
        return {"merchant": "Synthetic Market", "date": "2026-10-12",
                "total": None if self.missing_total else "10.00", "currency": "PKR"}


def test_real_digital_pdf_extraction():
    assert "TOTAL 4850.00" in extract_pdf_text(pdf("SYNTHETIC RECEIPT\nTOTAL 4850.00"))


def test_digital_pdf_calls_text_parser_not_image():
    service = ServiceDouble()
    result = asyncio.run(process_invoice(pdf("TOTAL 10.00"), "application/pdf", service))
    assert result.processing_path == "digital text"
    assert result.requires_review and result.draft is not None
    assert service.calls[0][0] == "text" and len(service.calls) == 1


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg"])
def test_images_try_dnotifier_first_then_manual_without_ocr_dependency(mime):
    service = ServiceDouble()
    result = asyncio.run(process_invoice(b"synthetic image bytes", mime, service))
    assert service.calls[0][0] == "image" and len(service.calls) == 1
    assert result.processing_path == "manual" and result.attempted_path == "DNotifier image"
    assert result.draft is None and "unverified" in result.failure and result.requires_review


def test_scanned_page_is_rendered_to_png_then_tries_primary_image_path():
    service = ServiceDouble()
    result = asyncio.run(process_invoice(pdf(), "application/pdf", service))
    assert service.calls == [("image", "image/png", b"\x89PNG\r\n\x1a\n")]
    assert result.processing_path == "manual" and result.failure


def test_successful_image_path_with_test_double_still_requires_review():
    result = asyncio.run(process_invoice(b"synthetic", "image/png", ServiceDouble(image_failure=False)))
    assert result.processing_path == "DNotifier image" and result.requires_review


def test_insufficient_image_fields_are_manual_and_draft_is_preserved():
    result = asyncio.run(process_invoice(
        b"synthetic", "image/png", ServiceDouble(image_failure=False, missing_total=True)))
    assert result.processing_path == "manual" and result.draft["total"] is None
    assert "required fields missing" in result.failure


@pytest.mark.parametrize("data,mime", [(b"not a PDF", "application/pdf"), (b"", "image/png"),
                                      (b"x", "text/plain"), (b"x" * (10 * 1024 * 1024 + 1), "image/png")])
def test_invalid_uploads_offer_manual_review_without_ai(data, mime):
    service = ServiceDouble()
    result = asyncio.run(process_invoice(data, mime, service))
    assert result.processing_path == "manual" and result.failure
    assert result.draft is None and not service.calls


def test_page_limit_and_scanned_multipage_limit_are_explicit():
    with pytest.raises(InvalidAIOutput, match="1–4 pages"):
        extract_pdf_text(pdf(pages=5))
    with pytest.raises(InvalidAIOutput, match="Multi-page"):
        render_scanned_page(pdf(pages=2))


def test_pdf_ai_failure_is_recoverable():
    class FailedService(ServiceDouble):
        async def extract_invoice(self, text):
            raise InvalidAIOutput("AI output failed validation.")

    result = asyncio.run(process_invoice(pdf("TOTAL 10.00"), "application/pdf", FailedService()))
    assert result.processing_path == "manual" and result.attempted_path == "digital text"
    assert result.draft is None and result.failure


def test_mixed_pdf_is_not_silently_parsed_as_partial_digital_text():
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "SUBTOTAL 10.00")
        document.new_page()
        data = document.tobytes()
    service = ServiceDouble()
    result = asyncio.run(process_invoice(data, "application/pdf", service))
    assert result.processing_path == "manual" and "Multi-page" in result.failure
    assert not service.calls


def test_encrypted_pdf_is_manual_not_crash():
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "SYNTHETIC TOTAL 10.00")
        data = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                                owner_pw="synthetic-owner", user_pw="synthetic-user")
    service = ServiceDouble()
    result = asyncio.run(process_invoice(data, "application/pdf", service))
    assert result.processing_path == "manual" and "unencrypted" in result.failure
    assert not service.calls


def test_unexpected_library_failure_does_not_leak_raw_document_details():
    class FailedService(ServiceDouble):
        async def extract_invoice(self, text):
            raise RuntimeError("synthetic private receipt text")

    result = asyncio.run(process_invoice(pdf("TOTAL 10.00"), "application/pdf", FailedService()))
    assert result.processing_path == "manual"
    assert "synthetic private" not in result.failure


@pytest.mark.parametrize("wrong_total", [False, True])
def test_live_probe_checks_source_facts_not_just_successful_schema(wrong_total):
    class ProbeServiceDouble:
        calls = 0

        async def extract_invoice(self, text):
            self.calls += 1
            incomplete = "Date is illegible" in text
            return {
                "merchant": "Synthetic Grocery Market", "invoice_number": None if incomplete else "DEMO-1042",
                "date": None if incomplete else "2026-10-12", "total": "4500.00" if wrong_total else "4850.00",
                "currency": "PKR", "suggested_category": "Groceries",
                "missing_fields": ["date", "invoice_number"] if incomplete else [],
            }

    service = ProbeServiceDouble()
    result = asyncio.run(verify_invoice_interpretation(service))
    assert result["verified"] is not wrong_total
    assert service.calls == 3
    assert not result["expense_committed"]
    assert result["checks"]["missing_date_and_invoice_number_preserved"]
