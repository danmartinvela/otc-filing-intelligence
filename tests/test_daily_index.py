from datetime import date

from src.sec_ingestion.daily_index import build_index_url, get_quarter


def test_get_quarter_q1():
    assert get_quarter(1) == 1
    assert get_quarter(2) == 1
    assert get_quarter(3) == 1


def test_get_quarter_q2():
    assert get_quarter(4) == 2
    assert get_quarter(5) == 2
    assert get_quarter(6) == 2


def test_get_quarter_q3():
    assert get_quarter(7) == 3
    assert get_quarter(8) == 3
    assert get_quarter(9) == 3


def test_get_quarter_q4():
    assert get_quarter(10) == 4
    assert get_quarter(11) == 4
    assert get_quarter(12) == 4


def test_build_index_url():
    url = build_index_url(date(2024, 5, 15))
    assert url == (
        "https://www.sec.gov/Archives/edgar/daily-index"
        "/2024/QTR2/master.20240515.idx"
    )


def test_build_index_url_q1():
    url = build_index_url(date(2023, 1, 3))
    assert "QTR1" in url
    assert "20230103" in url


def test_build_index_url_q4():
    url = build_index_url(date(2023, 12, 31))
    assert "QTR4" in url
    assert "20231231" in url
