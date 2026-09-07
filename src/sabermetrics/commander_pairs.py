"""Commander pairing rules and stable, order-independent deck identities."""

from __future__ import annotations

import hashlib
import re
from typing import Any


def pair_id(oracle_ids: list[str]) -> str:
    return (
        "pair-"
        + hashlib.sha256("\n".join(sorted(set(oracle_ids))).encode()).hexdigest()[:32]
    )


def _abilities(card: dict[str, Any]) -> set[str]:
    # Match ability lines, never reminder text that merely mentions partner.
    text = str(card.get("oracle_text") or "").casefold().replace("’", "'")
    return {
        re.sub(r"\s*\(.*", "", line).strip().rstrip(".") for line in text.splitlines()
    }


def compatible_pair(first: dict[str, Any], second: dict[str, Any]) -> bool:
    if (first.get("oracle_id") or first.get("name")) == (
        second.get("oracle_id") or second.get("name")
    ):
        return False
    a, b = _abilities(first), _abilities(second)
    if "partner" in a and "partner" in b:
        return True

    # Named partner variants only match the same variant. Friends forever
    # includes the original wording and the newer Partner—Friends forever.
    def variants(abilities: set[str]) -> set[str]:
        found = {
            line.split("—", 1)[1].strip()
            for line in abilities
            if line.startswith("partner—")
        }
        if "friends forever" in abilities:
            found.add("friends forever")
        return found

    if variants(a) & variants(b):
        return True
    if ("partner with " + str(second.get("name", "")).casefold()) in a and (
        "partner with " + str(first.get("name", "")).casefold()
    ) in b:
        return True
    for leader, companion, abilities in ((first, second, a), (second, first, b)):
        types = str(companion.get("type_line") or "").casefold()
        if "choose a background" in abilities and all(
            t in types for t in ("legendary", "enchantment", "background")
        ):
            return True
        # Doctor's companion requires exactly the Time Lord Doctor creature
        # types, not any creature whose type line happens to mention Doctor.
        if (
            "doctor's companion" in abilities
            and "legendary" in types
            and "creature" in types
        ):
            subtypes = types.split("—", 1)[-1].strip()
            if subtypes == "time lord doctor":
                return True
    return False
