"""Category rules: decide which folder a file belongs in."""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Default extension -> category map.
# Keys are lowercase, without the leading dot.
# ---------------------------------------------------------------------------
DEFAULT_CATEGORIES: Dict[str, List[str]] = {
    "Documents": [
        "doc", "docx", "dot", "dotx", "odt", "rtf", "txt", "md", "markdown",
        "pages", "tex", "wps", "log",
    ],
    "Spreadsheets": [
        "xls", "xlsx", "xlsm", "xlt", "xltx", "csv", "tsv", "ods", "numbers",
    ],
    "Presentations": ["ppt", "pptx", "pot", "potx", "odp", "key"],
    "PDF": ["pdf"],
    "Images": [
        "jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp", "heic",
        "heif", "svg", "ico", "raw", "cr2", "nef", "arw", "dng", "psd", "ai",
        "avif",
    ],
    "Video": [
        "mp4", "mov", "avi", "mkv", "wmv", "flv", "webm", "m4v", "mpg",
        "mpeg", "3gp", "m2ts", "mts",
    ],
    # Note: ".ts" is deliberately left to Code. It is an MPEG transport stream
    # far less often than it is TypeScript, and mis-filing source into Video
    # is the more annoying mistake of the two.
    "Audio": [
        "mp3", "wav", "flac", "aac", "m4a", "ogg", "oga", "wma", "aiff",
        "aif", "opus", "mid", "midi",
    ],
    "Archives": [
        "zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "tbz", "xz", "zst",
        "iso", "cab",
    ],
    "Installers": [
        "dmg", "pkg", "exe", "msi", "msix", "deb", "rpm", "appimage", "apk",
        "snap", "flatpak",
    ],
    "Code": [
        "py", "pyw", "ipynb", "js", "mjs", "cjs", "ts", "tsx", "jsx", "java",
        "class", "jar", "c", "h", "cpp", "cc", "hpp", "cs", "go", "rs", "rb",
        "php", "swift", "kt", "kts", "scala", "sh", "bash", "zsh", "fish",
        "ps1", "bat", "cmd", "sql", "r", "m", "lua", "pl", "vue", "svelte",
        "html", "htm", "css", "scss", "sass", "less", "json", "jsonl", "xml",
        "yaml", "yml", "toml", "ini", "cfg", "conf", "env", "gradle",
    ],
    "Ebooks": ["epub", "mobi", "azw", "azw3", "djvu", "fb2", "cbz", "cbr"],
    "Fonts": ["ttf", "otf", "ttc", "woff", "woff2", "eot"],
    "Design": ["fig", "sketch", "xd", "blend", "obj", "fbx", "stl", "3ds", "dwg", "dxf"],
    "Torrents": ["torrent"],
}

# Files that are still being written by a browser / download manager.
# Never touch these, whatever their "real" extension looks like.
INCOMPLETE_SUFFIXES = (
    ".crdownload", ".part", ".partial", ".download", ".tmp", ".temp",
    ".!ut", ".opdownload", ".aria2",
)

# Multi-part extensions that should be treated as a unit.
COMPOUND_EXTENSIONS = (
    ".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst",
)

FALLBACK_CATEGORY = "Other"
SCREENSHOT_CATEGORY = "Screenshots"

# Screenshot filename patterns across macOS / Windows / common tools.
# Matched against a whitespace-normalised, casefolded filename.
SCREENSHOT_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"^screenshot[ _-]"),          # macOS (modern), Android
    re.compile(r"^screenshot \(\d+\)"),        # Windows "Screenshot (3).png"
    re.compile(r"^screen shot "),              # macOS (legacy)
    re.compile(r"^screen recording "),         # macOS screen recordings
    re.compile(r"^cleanshot "),                # CleanShot X
    re.compile(r"^snipaste[ _-]"),             # Snipaste
    re.compile(r"^shottr[ _-]"),               # Shottr
    re.compile(r"^截屏"),                       # macOS 中文
    re.compile(r"^屏幕快照"),                    # macOS 中文 (legacy)
    re.compile(r"^屏幕截图"),                    # Windows 中文
    re.compile(r"^录屏"),
)


def normalise_for_match(name: str) -> str:
    """Fold a filename into a stable form for pattern matching.

    macOS writes screenshot names with U+202F (narrow no-break space) between
    the time and am/pm, and stores filenames in NFD while most other systems
    use NFC.  Both make naive ``startswith`` checks fail in confusing ways, so
    every unicode space becomes a plain space and the string is normalised to
    NFC before matching.
    """
    text = unicodedata.normalize("NFC", name)
    # Zs category covers U+0020, U+00A0, U+202F, U+3000 (ideographic space), etc.
    text = "".join(
        " " if unicodedata.category(ch) == "Zs" else ch
        for ch in text
    )
    return " ".join(text.split()).casefold()


def split_extension(name: str) -> Tuple[str, str]:
    """Split ``name`` into (stem, extension). Extension keeps its dot, lowercased.

    Handles compound extensions such as ``.tar.gz`` as a single unit so an
    archive does not end up filed as a bare gzip stream.
    """
    lowered = name.lower()
    for compound in COMPOUND_EXTENSIONS:
        if lowered.endswith(compound) and len(name) > len(compound):
            return name[: -len(compound)], compound
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        # No dot, leading dot (dotfile), or trailing dot -> no usable extension.
        return name, ""
    return name[:dot], lowered[dot:]


def is_incomplete(name: str) -> bool:
    """True for partially-downloaded files that must never be moved."""
    lowered = name.lower()
    return lowered.endswith(INCOMPLETE_SUFFIXES)


def looks_like_screenshot(name: str) -> bool:
    folded = normalise_for_match(name)
    return any(pattern.search(folded) for pattern in SCREENSHOT_PATTERNS)


class Ruleset:
    """Resolves a filename to a destination category."""

    def __init__(
        self,
        categories: Optional[Dict[str, List[str]]] = None,
        fallback: str = FALLBACK_CATEGORY,
        detect_screenshots: bool = True,
        screenshot_category: str = SCREENSHOT_CATEGORY,
    ) -> None:
        self.categories = categories if categories is not None else DEFAULT_CATEGORIES
        self.fallback = fallback
        self.detect_screenshots = detect_screenshots
        self.screenshot_category = screenshot_category
        self._lookup = self._build_lookup(self.categories)

    @staticmethod
    def _build_lookup(categories: Dict[str, List[str]]) -> Dict[str, str]:
        lookup: Dict[str, str] = {}
        for category, extensions in categories.items():
            for ext in extensions:
                key = ext.lower().lstrip(".")
                if not key:
                    continue
                # First definition wins, so a user config listing a category
                # earlier can claim an extension from a later one.
                lookup.setdefault(key, category)
        return lookup

    def category_for(self, name: str) -> str:
        """Return the destination category folder for ``name``."""
        if self.detect_screenshots and looks_like_screenshot(name):
            return self.screenshot_category
        _, ext = split_extension(name)
        if not ext:
            return self.fallback
        key = ext.lstrip(".")
        if key in self._lookup:
            return self._lookup[key]
        # Compound extension such as .tar.gz -> try the final part.
        if "." in key:
            tail = key.rsplit(".", 1)[-1]
            if tail in self._lookup:
                return self._lookup[tail]
        return self.fallback

    def known_extensions(self) -> Dict[str, str]:
        return dict(self._lookup)
