"""Locate and run the external OCR binaries (Poppler, Tesseract) as argument-list subprocesses."""

import os
import subprocess
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 120


class OcrToolError(RuntimeError):
    """An external OCR binary failed, timed out, or was not found."""


def poppler_binary(name: str) -> str:
    """Path to a Poppler tool; POPPLER_PATH is its bin directory, otherwise the tool must be on PATH."""
    poppler_dir = os.environ.get("POPPLER_PATH")
    return str(Path(poppler_dir) / name) if poppler_dir else name


def tesseract_binary() -> str:
    return os.environ.get("TESSERACT_CMD", "tesseract")


def run_tool(args: list[str], timeout: int = DEFAULT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=True
        )
    except FileNotFoundError as exc:
        raise OcrToolError(f"{args[0]} not found; set POPPLER_PATH / TESSERACT_CMD or add it to PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise OcrToolError(f"{Path(args[0]).name} timed out after {timeout}s") from exc
    except subprocess.CalledProcessError as exc:
        raise OcrToolError(f"{Path(args[0]).name} exited {exc.returncode}: {exc.stderr.strip()}") from exc
