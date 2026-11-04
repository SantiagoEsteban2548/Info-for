import re
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from helpers import frame
from tradingagent.report import (ReportItem, build_daily_pdf, expected_range, fmt, items_from_reports,
                                 range_coverage)
from tradingagent.weekly import pending_orders


def walk(n=300, seed=3, sigma=0.02):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, sigma, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
    idx = pd.date_range("2026-01-01", periods=n, freq="1D", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx)


def setup_frame():
    bars = [(100 + i - 0.5, 100 + i + 1, 100 + i - 1, 100 + i) for i in range(60)] + [(159, 160, 135, 158)]
    return frame(bars, freq="1D")


def pages(path):
    return len(re.findall(rb"/Type /Page\b", path.read_bytes()))


def test_expected_range_is_log_symmetric_and_widens():
    df = walk()
    lo1, hi1 = expected_range(df, 1)
    lo5, hi5 = expected_range(df, 5)
    last = df["close"].iloc[-1]
    assert lo1 < last < hi1 and lo5 < lo1 and hi5 > hi1
    assert lo1 * hi1 == pytest.approx(last ** 2)


def test_coverage_near_68_percent_for_gaussian_returns():
    assert 0.62 <= range_coverage(walk(n=4000)) <= 0.74
    assert range_coverage(walk(n=40)) is None


def test_fmt():
    assert fmt(12345.678) == "12,345.68" and fmt(0.123456) == "0.1235"


def test_pdf_has_summary_plus_one_page_per_asset(tmp_path):
    df = setup_frame()
    [order] = pending_orders(df, "BTC/USDT")
    items = [
        ReportItem("Bitcoin", "BTC/USDT:USDT", df, order, technical="Setup S1 largo",
                   verdict={"veredicto": "alcista", "conviccion": 72, "coincide_con_tecnico": True,
                            "razones": ["Entrada de ETF", "Tendencia intacta"], "alertas": ["CPI el jueves"]},
                   decision="orden colocada con riesgo 1% (1.00 USD)"),
        ReportItem("Oro", "XAU/USDT:USDT", walk()),
    ]
    out = build_daily_pdf(items, tmp_path / "sub" / "informe.pdf", now=datetime(2026, 11, 4), equity=100)
    assert out.read_bytes().startswith(b"%PDF") and out.stat().st_size > 20_000
    assert pages(out) == 3


def test_pdf_with_no_items_still_renders_summary(tmp_path):
    out = build_daily_pdf([], tmp_path / "vacio.pdf", now=datetime(2026, 11, 4))
    assert pages(out) == 1


def test_items_from_reports_skips_missing_candles_and_finds_order():
    df = setup_frame()
    reports = [SimpleNamespace(asset="BTC", symbol="BTC/USDT:USDT", technical="t", verdict={}, decision="d"),
               SimpleNamespace(asset="XAU", symbol="XAU/USDT:USDT", technical="t", verdict=None, decision="d")]
    items = items_from_reports(reports, {"BTC/USDT:USDT": df}, {"BTC": "Bitcoin"})
    assert [i.name for i in items] == ["Bitcoin"] and items[0].order is not None


def test_cli_builds_pdf(tmp_path, monkeypatch, capsys):
    from tradingagent import pipeline, report

    monkeypatch.setattr(pipeline, "load_daily_candles", lambda asset: setup_frame())
    out = tmp_path / "x.pdf"
    report.main(["--assets", "BTC,XAU", "--out", str(out)])
    assert str(out) in capsys.readouterr().out and pages(out) == 3
