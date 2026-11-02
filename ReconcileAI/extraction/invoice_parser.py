"""
ReconcileAI – Invoice Parser
Extracts text from PDF invoices and Excel invoice files.
Structured extraction (field parsing) is delegated to llm_extractor.py.
"""
from __future__ import annotations
from pathlib import Path


def extract_pdf_text(filepath: str | Path) -> str:
    """Extract text from a digital PDF using PyMuPDF. Falls back to OCR for scanned pages."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        # Graceful degradation — return empty string so ingestion can continue
        import warnings
        warnings.warn("PyMuPDF not installed. PDF text extraction unavailable. Run: pip install PyMuPDF")
        return ""

    doc = fitz.open(str(filepath))
    texts = []
    for page in doc:
        text = page.get_text("text").strip()
        if not text:
            # Scanned page – attempt OCR
            text = _ocr_page(page)
        if text:
            texts.append(text)
    doc.close()
    return "\n".join(texts)


def _ocr_page(page) -> str:
    """Render a PyMuPDF page to an image and run Tesseract OCR."""
    try:
        import pytesseract
        from PIL import Image
        import io
        pix = page.get_pixmap(dpi=200)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        return pytesseract.image_to_string(img, lang="eng")
    except ImportError:
        return ""  # pytesseract or Pillow not installed
    except Exception:
        return ""


def extract_excel_invoices(filepath: str | Path) -> list[dict]:
    """
    Parse invoices stored in an Excel workbook.
    Each row is treated as one invoice. Returns list of raw dicts.
    """
    import pandas as pd
    df = pd.read_excel(str(filepath), dtype=str)
    df = df.dropna(how="all")
    df.columns = [c.strip().lower() for c in df.columns]

    invoices = []
    for _, row in df.iterrows():
        invoices.append({k: (v if v != "nan" else None) for k, v in row.to_dict().items()})
    return invoices
