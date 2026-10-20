from pathlib import Path

import pytest

from converter.extraction import extract_order, parse_tesseract_tsv
from converter.profiles import load_profile
from converter.worker import runner

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def clear_runner_stop_request():
    """Every app shutdown sets the runner's process-wide stop flag; keep it from leaking into the next test."""
    runner.reset_stop_request()
    yield
    runner.reset_stop_request()


@pytest.fixture
def profile():
    return load_profile("odoo_sale_order_v1")


@pytest.fixture
def synthetic_words():
    return parse_tesseract_tsv((FIXTURES / "odoo_sale_order_S08824.tsv").read_text(encoding="utf-8"))


@pytest.fixture
def letterhead_words():
    return parse_tesseract_tsv((FIXTURES / "odoo_sale_order_S07002_letterhead.tsv").read_text(encoding="utf-8"))


@pytest.fixture
def vat_no_total_bar_words():
    """VAT layout (Untaxed Amount + VAT rows) whose Total bar was not OCR'd at all."""
    return parse_tesseract_tsv((FIXTURES / "odoo_sale_order_S08824_vat_no_total_bar.tsv").read_text(encoding="utf-8"))


@pytest.fixture
def synthetic_order(synthetic_words, profile):
    return extract_order(synthetic_words, profile, page_number=1)


@pytest.fixture
def template_path():
    return FIXTURES / "sample_template.xlsx"
