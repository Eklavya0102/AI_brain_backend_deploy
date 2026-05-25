"""
AI Team Brain - File Processing Service
=========================================
Extracts text from PDF, DOCX, TXT, and pasted content.
"""

import os
import re
from pathlib import Path
from loguru import logger
from typing import Optional


def extract_text_from_file(file_path: str, file_type: str) -> Optional[str]:
    """Extract plain text from uploaded files."""
    try:
        if file_type == "pdf":
            return _extract_pdf(file_path)
        elif file_type == "docx":
            return _extract_docx(file_path)
        elif file_type in ("txt", "md", "text"):
            return _extract_txt(file_path)
        else:
            logger.warning(f"Unsupported file type: {file_type}")
            return None
    except Exception as e:
        logger.error(f"File extraction failed for {file_path}: {e}")
        return None


def _extract_pdf(file_path: str) -> str:
    try:
        import PyPDF2
        text_parts = []
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text.strip())
        return "\n\n".join(text_parts)
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        return ""


def _extract_docx(file_path: str) -> str:
    try:
        from docx import Document
        doc = Document(file_path)
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)
    except Exception as e:
        logger.error(f"DOCX extraction error: {e}")
        return ""


def _extract_txt(file_path: str) -> str:
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as e:
        logger.error(f"TXT extraction error: {e}")
        return ""


def clean_text(text: str) -> str:
    """Normalize whitespace and clean extracted text."""
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def get_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    mapping = {"pdf": "pdf", "docx": "docx", "doc": "docx", "txt": "txt", "md": "txt"}
    return mapping.get(ext, "txt")


ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt", "md"}


def allowed_file(filename: str) -> bool:
    return "." in filename and Path(filename).suffix.lower().lstrip(".") in ALLOWED_EXTENSIONS
