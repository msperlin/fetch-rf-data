from tqdm import tqdm

from rfbpy.progress import BAR_FORMAT


def test_bar_format_uses_two_decimals():
    assert "{percentage:3.2f}" in BAR_FORMAT


def test_format_meter_renders_two_decimals():
    text = tqdm.format_meter(45, 100, 1.0, bar_format=BAR_FORMAT, ncols=80)
    assert "45.00%" in text


def test_format_meter_rounds_two_decimals():
    text = tqdm.format_meter(4512, 10000, 1.0, bar_format=BAR_FORMAT, ncols=80)
    assert "45.12%" in text
