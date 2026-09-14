"""Cross-platform filename safety.

The goal is *portability*, not anglicisation: CJK characters, accents and
emoji are all perfectly legal filenames and are left alone.  What gets fixed
is the set of characters that genuinely break on one platform or another --
Windows-reserved characters, control characters, exotic unicode spaces, and
names that Windows refuses outright (``CON``, trailing dots, ...).
"""
from __future__ import annotations

import unicodedata
from typing import Tuple

from .rules import COMPOUND_EXTENSIONS

# Illegal on Windows (NTFS/FAT). ':' and '/' also confuse macOS Finder, which
# displays a stored '/' as ':' and vice versa -- the single most common source
# of "my folder name looks different in Terminal" confusion.
WINDOWS_RESERVED_CHARS = '<>:"/\\|?*'

# Device names Windows refuses regardless of extension.
WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM%d" % i for i in range(1, 10)]
    + ["LPT%d" % i for i in range(1, 10)]
)

REPLACEMENT = "-"
MAX_COMPONENT_BYTES = 240  # leave headroom under the common 255-byte limit


def _split_preserving_case(name: str) -> Tuple[str, str]:
    """Split into (stem, extension) keeping the extension's original case.

    ``rules.split_extension`` lowercases the extension because it feeds a
    lookup table; renaming must not, or ``photo.JPG`` would be "fixed" into
    ``photo.jpg`` and a case-insensitive filesystem would see a needless
    collision.
    """
    lowered = name.lower()
    for compound in COMPOUND_EXTENSIONS:
        if lowered.endswith(compound) and len(name) > len(compound):
            return name[: -len(compound)], name[-len(compound):]
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return name, ""
    return name[:dot], name[dot:]


def _strip_control_chars(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cc")


def _normalise_spaces(text: str) -> str:
    """Turn every exotic unicode space into a plain ASCII space.

    U+202F (narrow no-break space) in macOS screenshot names and U+00A0 from
    pasted web content both look identical to a normal space but compare
    unequal, which makes shell globs and scripts silently miss files.

    Runs of spaces are deliberately *not* collapsed: two spaces in a row are
    legal on every platform, so squeezing them would rename a file that has
    nothing wrong with it.
    """
    return "".join(
        " " if unicodedata.category(ch) == "Zs" else ch
        for ch in text
    )


def is_portable(name: str) -> bool:
    """True if ``name`` is safe on macOS, Windows and Linux as-is."""
    return safe_name(name) == name


def diagnose(name: str) -> Tuple[str, ...]:
    """Return human-readable reasons why ``name`` is not portable."""
    problems = []
    if any(ch in WINDOWS_RESERVED_CHARS for ch in name):
        bad = sorted({ch for ch in name if ch in WINDOWS_RESERVED_CHARS})
        problems.append("Windows-illegal character(s): " + " ".join(bad))
    if any(unicodedata.category(ch) == "Cc" for ch in name):
        problems.append("control character")
    exotic = sorted({
        "U+%04X" % ord(ch)
        for ch in name
        if unicodedata.category(ch) == "Zs" and ch != " "
    })
    if exotic:
        problems.append("non-standard space: " + " ".join(exotic))
    if name != unicodedata.normalize("NFC", name):
        problems.append("decomposed unicode (NFD)")
    stem, _ = _split_preserving_case(name)
    if stem.upper() in WINDOWS_RESERVED_NAMES:
        problems.append("Windows reserved device name")
    if name != name.rstrip(" .") and name.rstrip(" ."):
        problems.append("trailing space or dot (Windows strips it)")
    if len(name.encode("utf-8", "ignore")) > MAX_COMPONENT_BYTES:
        problems.append("longer than %d bytes" % MAX_COMPONENT_BYTES)
    return tuple(problems)


def safe_name(name: str, replacement: str = REPLACEMENT) -> str:
    """Return a portable version of ``name``.

    Non-ASCII letters are preserved -- only genuinely unsafe characters are
    substituted.  Always returns a non-empty string.
    """
    text = unicodedata.normalize("NFC", name)
    text = _strip_control_chars(text)
    text = _normalise_spaces(text)
    text = "".join(replacement if ch in WINDOWS_RESERVED_CHARS else ch for ch in text)

    # Windows silently drops trailing dots and spaces, which turns
    # "report ." into "report" and can collide with an existing file.
    text = text.rstrip(" .")
    if not text:
        return "untitled"

    stem, ext = _split_preserving_case(text)
    if stem.upper() in WINDOWS_RESERVED_NAMES:
        stem = stem + "_file"

    # Trim to a byte budget without splitting a multi-byte character.
    ext_bytes = len(ext.encode("utf-8"))
    budget = max(1, MAX_COMPONENT_BYTES - ext_bytes)
    encoded = stem.encode("utf-8")
    if len(encoded) > budget:
        stem = encoded[:budget].decode("utf-8", "ignore").rstrip() or "untitled"

    result = stem + ext
    return result or "untitled"
