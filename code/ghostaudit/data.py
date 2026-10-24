"""Locations and loaders for the participant-facing dataset."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"


def parse_time(value: str) -> datetime:
    """Parse the dataset's UTC ISO-8601 timestamps ('...Z') into aware datetimes."""
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def read_csv(name: str, dataset_dir: Path = DATASET_DIR) -> list[dict[str, str]]:
    with open(dataset_dir / name, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
