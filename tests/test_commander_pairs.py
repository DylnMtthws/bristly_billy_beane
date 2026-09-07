"""Partner variants follow CR 702.124, including named partner restrictions."""

import pytest

from sabermetrics.commander_pairs import compatible_pair, pair_id


@pytest.mark.parametrize(
    "first,second,expected",
    [
        ("Partner (You can have two commanders.)", "Partner", True),
        ("Partner", "Partner with First", False),
        ("Partner with Second", "Partner with First", True),
        ("Partner with Someone Else", "Partner with First", False),
        ("Friends forever", "Partner—Friends forever", True),
        ("Partner—Survivors", "Partner—Friends forever", False),
        ("Partner—Survivors", "Partner—Survivors", True),
        ("Partner", "Partner—Friends forever", False),
        ("An ability mentioning partner.", "Partner", False),
    ],
)
def test_partner_variants(first, second, expected):
    a = {"name": "First", "oracle_text": first}
    b = {"name": "Second", "oracle_text": second}
    assert compatible_pair(a, b) is expected
    assert compatible_pair(b, a) is expected
    assert not compatible_pair(a, a)


def test_background_and_doctor_types():
    choose = {"name": "Leader", "oracle_text": "Choose a Background"}
    background = {
        "name": "Background",
        "type_line": "Legendary Enchantment — Background",
    }
    assert compatible_pair(choose, background)
    assert not compatible_pair(
        choose, {**background, "type_line": "Enchantment — Background"}
    )
    companion = {"name": "Companion", "oracle_text": "Doctor’s companion"}
    doctor = {"name": "Doctor", "type_line": "Legendary Creature — Time Lord Doctor"}
    assert compatible_pair(companion, doctor)
    assert not compatible_pair(
        companion, {**doctor, "type_line": "Legendary Creature — Human Doctor"}
    )
    assert not compatible_pair(
        companion,
        {**doctor, "type_line": "Legendary Creature — Time Lord Doctor Pilot"},
    )


def test_identity_is_order_independent():
    assert pair_id(["oracle-tymna", "oracle-rograkh"]) == pair_id(
        ["oracle-rograkh", "oracle-tymna"]
    )
    assert pair_id(["oracle-tymna", "oracle-rograkh"]) != pair_id(
        ["oracle-silas", "oracle-rograkh"]
    )
