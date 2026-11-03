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
        import pymupdf as fitz  # PyMuPDF ≥ 1.24
    except ImportError:
        try:
            import fitz  # older PyMuPDF
        except ImportError:
            fitz = None
    if fitz is None:
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
    Parse invoices stored in an Excel workbook or CSV file.
    Each row is treated as one invoice. Returns list of raw dicts with
    lower-cased, whitespace-normalised column names.
    """
    import pandas as pd
    p = Path(filepath)
    if p.suffix.lower() == ".csv":
        df = pd.read_csv(str(p), dtype=str)
    else:
        df = pd.read_excel(str(p), dtype=str)
    df = df.dropna(how="all")
    df.columns = [" ".join(str(c).strip().lower().replace("_", " ").split()) for c in df.columns]

    invoices = []
    for _, row in df.iterrows():
        invoices.append({k: (None if pd.isna(v) or str(v).strip().lower() in ("", "nan", "none") else str(v).strip())
                         for k, v in row.to_dict().items()})
    return invoices


def extract_pdf_bytes_text(file_bytes: bytes) -> str:
    """Convenience wrapper: extract text from PDF bytes."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        path = tmp.name
    try:
        return extract_pdf_text(path)
    finally:
        Path(path).unlink(missing_ok=True)
