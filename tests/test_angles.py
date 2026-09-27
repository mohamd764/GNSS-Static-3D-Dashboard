import pytest

from gnss_check.angles import decimal_to_dms, dms_to_decimal, normalize_0_360, normalize_pm180


def test_dms_to_decimal():
    assert dms_to_decimal(10, 30, 0) == pytest.approx(10.5)
    assert dms_to_decimal("359", "59", "59.99") == pytest.approx(359 + 59 / 60 + 59.99 / 3600)
    for bad in [(None, 0, 0), (360, 0, 0), (-1, 0, 0), (0, 60, 0), (0, 0, 60), ("a", 0, 0)]:
        assert dms_to_decimal(*bad) is None


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.0, "000° 00' 00.00\""),
        (10.5, "010° 30' 00.00\""),
        (359.999999, "360° 00' 00.00\""),
        (10.999999, "011° 00' 00.00\""),  # no 60.00" after rounding
        (-1.5, "-001° 30' 00.00\""),  # sign applies to the whole angle
        (-0.25, "-000° 15' 00.00\""),
        (-0.0001, "-000° 00' 00.36\""),
        (None, "N/A"),
    ],
)
def test_decimal_to_dms(value, expected):
    assert decimal_to_dms(value) == expected


def test_decimal_to_dms_wrap360():
    assert decimal_to_dms(359.999999, wrap360=True) == "000° 00' 00.00\""
    assert decimal_to_dms(90.0, wrap360=True) == "090° 00' 00.00\""


def test_decimal_to_dms_signed():
    assert decimal_to_dms(1.5843464562, signed=True) == "+001° 35' 03.65\""
    assert decimal_to_dms(-0.5887985, signed=True) == "-000° 35' 19.67\""
    assert decimal_to_dms(-1e-9, signed=True) == "+000° 00' 00.00\""


def test_round_trip():
    for value in (0.0, 0.123456, 45.0, 123.987654, 359.5):
        text = decimal_to_dms(value, seconds_decimals=4)
        d, rest = text.split("° ")
        m, s = rest.split("' ")
        assert dms_to_decimal(int(d), int(m), float(s.rstrip('"'))) == pytest.approx(value, abs=1e-7)


def test_normalize():
    assert normalize_0_360(-10) == pytest.approx(350)
    assert normalize_0_360(720) == 0
    assert normalize_pm180(190) == pytest.approx(-170)
    assert normalize_pm180(-180) == pytest.approx(180)
