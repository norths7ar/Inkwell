"""Small, unambiguous text replacements without rewriting unaffected source text."""

import re

from pydantic import BaseModel, ConfigDict, Field


class TextEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search: str = Field(min_length=1, max_length=100_000)
    replace: str = Field(max_length=100_000)


_EQUIVALENTS = str.maketrans(
    {
        **dict.fromkeys("‘’‚‛", "'"),
        **dict.fromkeys("“”„‟", '"'),
        **dict.fromkeys("‐‑‒–—―−", "-"),
        **dict.fromkeys(
            "\u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000",
            " ",
        ),
    }
)


def _normalized(text: str) -> tuple[str, list[int]]:
    # Keep a source index for every retained character; never normalize the output.
    chars: list[str] = []
    offsets: list[int] = []
    trailing = {
        i
        for match in re.finditer(r"[^\S\r\n]+(?=\r?$)", text, re.MULTILINE)
        for i in range(match.start(), match.end())
    }
    for index, char in enumerate(text):
        if index in trailing or (char == "\r" and text[index : index + 2] == "\r\n"):
            continue
        chars.append(char.translate(_EQUIVALENTS))
        offsets.append(index)
    return "".join(chars), offsets


def apply_edits(content: str, edits: list[TextEdit]) -> str:
    """Apply edits in order; reject missing/ambiguous matches, including overlaps."""
    result = content
    for number, edit in enumerate(edits, 1):
        start = result.find(edit.search)
        if start >= 0:
            if result.find(edit.search, start + 1) >= 0:
                raise ValueError(f"Edit {number} is ambiguous; include more surrounding text")
            end = start + len(edit.search)
        else:
            normalized, offsets = _normalized(result)
            needle, _ = _normalized(edit.search)
            if not needle:
                raise ValueError(f"Edit {number} has no meaningful search text")
            match = normalized.find(needle)
            if match < 0:
                raise ValueError(f"Edit {number} search text was not found")
            if normalized.find(needle, match + 1) >= 0:
                raise ValueError(f"Edit {number} is ambiguous; include more surrounding text")
            start, end = offsets[match], offsets[match + len(needle) - 1] + 1
        result = result[:start] + edit.replace + result[end:]
    return result
