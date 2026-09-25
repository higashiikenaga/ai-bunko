from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = ROOT / "content" / "novels"
SITE_SRC = ROOT / "site"
OUT_DIR = ROOT / "_site"


def load_config() -> dict:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    authors_path = ROOT / "authors.yaml"
    if authors_path.exists():
        cfg["authors"] = yaml.safe_load(authors_path.read_text(encoding="utf-8")) or []
    return cfg
