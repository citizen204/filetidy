"""Configuration loading and merging."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .rules import DEFAULT_CATEGORIES, FALLBACK_CATEGORY, SCREENSHOT_CATEGORY

CONFIG_FILENAME = "filetidy.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "archive_name": "_Archive",
    "recursive": False,
    "include_hidden": False,
    "date_subfolders": False,
    "date_format": "%Y-%m",
    "min_age_seconds": 60,
    "sanitize_names": False,
    "detect_screenshots": True,
    "screenshot_category": SCREENSHOT_CATEGORY,
    "fallback_category": FALLBACK_CATEGORY,
    "excludes": [],
    "categories": DEFAULT_CATEGORIES,
}


def user_config_path() -> Path:
    """Config location, following each platform's convention."""
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "filetidy" / CONFIG_FILENAME
        return Path.home() / "AppData" / "Roaming" / "filetidy" / CONFIG_FILENAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "filetidy" / CONFIG_FILENAME
    return Path.home() / ".config" / "filetidy" / CONFIG_FILENAME


def candidate_paths(target_dir: Optional[Path] = None) -> List[Path]:
    """Config files in precedence order, lowest priority first."""
    paths = [user_config_path()]
    if target_dir:
        # A config sitting next to the files being organised wins, which makes
        # per-folder rules (a project inbox, a photo dump) easy.
        paths.append(Path(target_dir) / CONFIG_FILENAME)
    return paths


def load(explicit: Optional[Path] = None, target_dir: Optional[Path] = None) -> Dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    sources = [explicit] if explicit else candidate_paths(target_dir)
    for path in sources:
        if path and Path(path).is_file():
            with Path(path).open("r", encoding="utf-8") as handle:
                config = merge(config, json.load(handle))
    return config


def merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay onto base.

    ``categories`` is merged key by key so a user only has to name the
    categories they want to change, and can add an extension to one without
    restating the whole default map.  Setting a category to an empty list
    removes it.
    """
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if key == "categories" and isinstance(value, dict):
            categories = copy.deepcopy(result.get("categories", {}))
            for name, extensions in value.items():
                if extensions:
                    categories[name] = list(extensions)
                else:
                    categories.pop(name, None)
            result["categories"] = categories
        else:
            result[key] = value
    return result


def write_starter(path: Path) -> Path:
    """Write a commented-by-example starter config."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    starter = {
        "archive_name": "_Archive",
        "min_age_seconds": 60,
        "sanitize_names": False,
        "date_subfolders": False,
        "excludes": ["*.lnk", "~$*"],
        "categories": {
            "Invoices": ["pdf"],
            "Other": [],
        },
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(starter, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return path
