"""Text presence checks; preserve punctuation and meaningful joiners in text."""
from unicodedata import category


def has_text(value: object) -> bool:
    # strip() alone treats zero-width/BOM/control-only model output as text.
    # Only test presence: do not remove joiners from real words or emoji.
    return isinstance(value, str) and any(
        not character.isspace() and category(character) not in {"Cf", "Cc", "Cs", "Mn", "Me"}
        for character in value
    )
