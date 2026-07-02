"""Headless unit tests for fpga-isv's pure protocol/config/geometry helpers.

These import fpga_isv.viewer (which imports tkinter, but never creates a Tk() root), so they
run without a display -- safe in CI.
"""

import json

from fpga_isv import examples
from fpga_isv.viewer import (
    Board, button_value, decode_message, encode_control, led_on, mask)


# -- wire protocol parsing --------------------------------------------------
def test_decode_message_parses_each_event():
    assert decode_message(b'{"ev":"reg","t":1.0,"name":"btn","kind":"ctrl","width":1}') == {
        "ev": "reg", "t": 1.0, "name": "btn", "kind": "ctrl", "width": 1}
    assert decode_message(b'{"ev":"flag","t":2.0,"name":"leds","val":42}')["val"] == 42
    assert decode_message(b'{"ev":"time","t":3.0}')["ev"] == "time"


def test_decode_message_handles_whitespace_blank_and_garbage():
    assert decode_message(b"   \r\n") is None
    assert decode_message(b"") is None
    assert decode_message(b"not json") is None
    # trailing CR (from \r\n framing) is stripped before parsing
    assert decode_message(b'{"ev":"close","name":"x"}\r')["name"] == "x"


def test_encode_control_roundtrips_and_is_newline_framed():
    line = encode_control("btn_run", 1)
    assert line.endswith(b"\n")
    assert json.loads(line) == {"name": "btn_run", "val": 1}
    # values are coerced to int (the viewer may hand us a bool/str-ish)
    assert json.loads(encode_control("sw", True))["val"] == 1


# -- bit helpers ------------------------------------------------------------
def test_mask_keeps_low_bits():
    assert mask(0xFF, 4) == 0x0F
    assert mask(0b10110, 8) == 0b10110
    assert mask(0x1FF, 8) == 0xFF


def test_led_on_reads_individual_bits():
    val = 0b1010
    assert [led_on(val, b) for b in range(4)] == [False, True, False, True]


def test_led_on_active_low_inverts_polarity():
    # active_state "off" -> a 0 bit lights the LED, a 1 bit turns it off.
    val = 0b1010
    assert [led_on(val, b, active_low=True) for b in range(4)] == [True, False, True, False]
    # default stays active-high
    assert led_on(0b1, 0) is True and led_on(0b0, 0) is False


def test_button_value_maps_press_state_to_wire_value():
    # active-high (default, "pressed"): pressed -> 1, released -> 0
    assert button_value(True) == 1
    assert button_value(False) == 0
    # active-low ("released"): pressed -> 0, released -> 1 (wire idles high)
    assert button_value(True, active_low=True) == 0
    assert button_value(False, active_low=True) == 1


# -- geometry (Board._hit is a staticmethod -> no Tk needed) ----------------
def test_hit_circle():
    region = ("circle", 100, 100, 10)
    assert Board._hit(region, 100, 100) is True
    assert Board._hit(region, 108, 100) is True       # inside radius
    assert Board._hit(region, 120, 100) is False      # outside radius


def test_hit_rect():
    region = ("rect", 10, 20, 50, 60)
    assert Board._hit(region, 30, 40) is True
    assert Board._hit(region, 10, 20) is True         # on the corner (inclusive)
    assert Board._hit(region, 9, 40) is False
    assert Board._hit(region, 30, 61) is False


# -- bundled examples -------------------------------------------------------
def test_ulx3s_example_is_discoverable_and_valid():
    assert "ulx3s" in examples.list_examples()
    path = examples.example_config_path("ulx3s")
    with open(path) as f:
        cfg = json.load(f)
    assert cfg["title"] == "ULX3S"
    assert len(cfg["leds"]["items"]) == 8
    # the power button (mirrored to the "p" key) is active-low on this board
    pwr = next(b for b in cfg["buttons"] if b.get("key") == "p")
    assert pwr["active_state"] == "released"


def test_unknown_example_raises():
    try:
        examples.example_config_path("does-not-exist")
    except ValueError as e:
        assert "unknown example" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown example")
