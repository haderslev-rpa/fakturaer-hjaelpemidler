from datetime import date
from decimal import Decimal

from fakturadata import beregn_alder_fra_cpr
from ydelsesmatch import udtraek_beloeb_fra_bemaerkninger


def test_beloeb_fra_bemaerkninger():
    assert udtraek_beloeb_fra_bemaerkninger({"bemærkninger": "Beløb 35,15 kr."}) == Decimal("35.15")


def test_alder_under_18():
    assert beregn_alder_fra_cpr("0101101234", date(2026, 1, 1)) == 16
