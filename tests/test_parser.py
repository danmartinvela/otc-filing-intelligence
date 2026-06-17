from src.sec_ingestion.parser import parse_line, parse_master_idx

_VALID_LINE = (
    "0000001234|Acme Corp|8-K|2024-05-15"
    "|edgar/data/1234/0000001234-24-000001.txt"
)


def test_parse_line_valid():
    filing = parse_line(_VALID_LINE)
    assert filing is not None
    assert filing.cik == "0000001234"
    assert filing.company_name == "Acme Corp"
    assert filing.form_type == "8-K"
    assert filing.date_filed == "2024-05-15"
    assert filing.filename == "edgar/data/1234/0000001234-24-000001.txt"
    assert filing.filing_url == (
        "https://www.sec.gov/Archives/"
        "edgar/data/1234/0000001234-24-000001.txt"
    )


def test_parse_line_too_few_fields():
    assert parse_line("CIK|Company|Form|Date") is None


def test_parse_line_non_numeric_cik():
    assert parse_line("not|valid|8-K|2024-05-15|edgar/data/x/file.txt") is None


def test_parse_line_empty():
    assert parse_line("") is None


def test_parse_master_idx_full():
    content = (
        "CIK|Company Name|Form Type|Date Filed|Filename\n"
        "--------------------------------------------------------------------------------\n"
        "0000001234|Acme Corp|8-K|2024-05-15|edgar/data/1234/0000001234-24-000001.txt\n"
        "0000005678|Beta Inc|10-K|2024-05-15|edgar/data/5678/0000005678-24-000002.txt\n"
        "0000009999|Gamma Ltd|S-1|2024-05-15|edgar/data/9999/0000009999-24-000003.txt\n"
    )
    filings = parse_master_idx(content)
    assert len(filings) == 3
    assert filings[0].company_name == "Acme Corp"
    assert filings[1].form_type == "10-K"
    assert filings[2].form_type == "S-1"


def test_parse_master_idx_skips_header_and_dashes():
    content = (
        "Some header line\n"
        "Another header line\n"
        "CIK|Company Name|Form Type|Date Filed|Filename\n"
        "-----------------------------------------------------------\n"
        "0000001234|Acme Corp|8-K|2024-05-15|edgar/data/1234/file.txt\n"
    )
    filings = parse_master_idx(content)
    assert len(filings) == 1


def test_parse_master_idx_empty_content():
    assert parse_master_idx("") == []
