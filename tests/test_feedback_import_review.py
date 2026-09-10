"""Coordinator regression cases for pasted export fidelity."""

from sabermetrics.deck_text_import import parse_deck_text


def test_sideboard_header_never_places_cards_in_library():
    parsed = parse_deck_text(
        "Commander\n1 Kinnan, Bonder Prodigy\nDeck\n1 Sol Ring\nSideboard\n1 Negate\nMaybeboard\n1 Opt"
    )
    cards = {line.name: line for line in parsed.lines}
    assert cards["Negate"].zone == "Sideboard"
    assert cards["Opt"].zone == "Maybeboard"
    assert not cards["Negate"].commander


def test_full_double_face_name_is_not_treated_as_an_inline_comment():
    parsed = parse_deck_text(
        "// Export\n1 Fire // Ice\n1 Sea Gate Restoration // Sea Gate, Reborn"
    )
    assert [line.name for line in parsed.lines] == [
        "Fire // Ice",
        "Sea Gate Restoration // Sea Gate, Reborn",
    ]
