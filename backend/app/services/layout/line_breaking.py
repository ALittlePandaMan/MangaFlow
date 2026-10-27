"""Keep these boundary rules in sync with frontend lineBreaking.ts."""
import regex

OPENING = set("([{（［｛〈《「『【〔〖〘〚‘“")
CLOSING = set(")]},.!?:;%）］｝〉》」』】〕〗〙〛、。，．！？：；％’”ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮー々")
WORD_FIT_REFERENCE_SIZE = 10


def graphemes(text):
    return regex.findall(r"\X", text)


def word(character):
    return bool(character) and (character[0] in "'_’" or character[0].isascii() and character[0].isalnum()
                               or 0xC0 <= ord(character[0]) < 0x2E80 and character[0].isalnum())


def word_count(text):
    previous, count = False, 0
    for item in graphemes(text):
        current = word(item)
        count += current and not previous
        previous = current
    return count


def fitting_chunk_count(text):
    previous, count = False, 0
    for item in graphemes(text):
        current = word(item) or item in CLOSING or item == '"'
        count += current and not previous
        previous = current
    return count


def can_break(left, right, preserve_words=True):
    return not (left in OPENING or right in CLOSING or left == "\u00a0" or right == "\u00a0"
                or preserve_words and (word(left) and word(right) or left in {".", ",", ":"} and right.isascii() and right.isdigit())
                or left == right and left in {"…", "—"})


def wrap_paragraph(text, fits, preserve_words=True):
    items = graphemes(text)
    if not items:
        return [""]
    lines, start = [], 0
    while start < len(items):
        end = start + 1
        while end <= len(items) and fits(items[start:end]):
            end += 1
        if end > len(items):
            lines.append("".join(items[start:]))
            break
        end = max(start + 1, end - 1)
        boundary = end
        while boundary > start and boundary < len(items) and not can_break(items[boundary - 1], items[boundary], preserve_words):
            boundary -= 1
        if boundary > start:
            end = boundary
        lines.append("".join(items[start:end]).rstrip(" "))
        start = end
        while start < len(items) and items[start] == " ":
            start += 1
    return lines
