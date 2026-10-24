"""Plain-text handovers, PDF documents and PNG screenshots.

Handover rows are explicit ledgers ("- time | system | resource | type | result | subject").
PDFs and screenshots are business artifacts without a status field, so their records carry
state MENTIONED and the capture time from the manifest; structured formats were checked to have
in-file capture times equal to the manifest's in every case (docs/dataset_findings.md).
PDF records group a "Record" line with its "Detail" lines; table rows ("a | b | c") stand alone.
Screenshot records are built per OCR line: a resource ID plus the identifiers on the same line.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .model import (
    MENTIONED,
    ParseContext,
    derive_state,
    identifiers_from_text,
    parse_time_or_none,
    resource_ids_in,
)

HANDOVER_ROW = re.compile(
    r"^- (?P<time>\S+) \| (?P<system>[\w-]+) \| (?P<resource>[\w-]+) \| (?P<type>[A-Z_]+) "
    r"\| (?P<result>[A-Z_]+) \| (?P<subject>.+)$"
)
SNAPSHOT_HEADER = re.compile(r"^Snapshot:\s*(\S+)\s*$", re.MULTILINE)
OCR_SCALE = 2  # 2x grayscale upscaling read every tested identifier exactly; 1x and 3x missed some


def parse_text(ctx: ParseContext) -> None:
    report = ctx.report
    report.parser = "handover_text"
    text = ctx.path.read_text(encoding="utf-8")
    header = SNAPSHOT_HEADER.search(text)
    file_time = parse_time_or_none(header.group(1)) if header else None
    if file_time is None:
        report.warnings.append("no parseable 'Snapshot:' header; row times are used as written")
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            report.note("blank_line")
            continue
        report.units_total += 1
        match = HANDOVER_ROW.match(line.strip())
        if not match:
            if line.strip().startswith("- "):
                report.warnings.append(f"line {number}: list row does not match the handover layout: {line[:120]!r}")
                report.account("unparsed_row")
            else:
                report.account("heading_line")
            continue
        when = parse_time_or_none(match["time"])
        if when is None:
            report.warnings.append(f"line {number}: unparseable row time {match['time']!r}; record has no usable time")
        fields = {
            "recorded_at": match["time"], "system": match["system"], "resource_id": match["resource"],
            "record_type": match["type"], "result": match["result"], "subject_reference": match["subject"],
        }
        state, basis = derive_state(fields)
        if state is None:
            report.warnings.append(f"line {number}: unmapped state value {basis}")
        ctx.add_record(
            location=f"line {number}", system_id=match["system"], resource_id=match["resource"],
            collection="handover_check", record_type=match["type"], state=state, state_basis=basis,
            observed_at=when, time_basis="record:recorded_at" if when else "record:recorded_at:unparseable",
            identifiers=frozenset(identifiers_from_text(match["subject"])), fields=fields,
        )
        report.units_used += 1


def _manifest_time(ctx: ParseContext):
    if ctx.captured_at is None:
        ctx.report.warnings.append("manifest has no usable capture time; records cannot establish state")
        return None, "none"
    return ctx.captured_at, "manifest:captured_at"


def _emit_mentions(ctx: ParseContext, chunk: list[str], location: str, extraction: str, when, basis) -> None:
    """Records for one chunk of text; the chunk's lines count as used or as explained once."""
    text = " ".join(chunk)
    resources = resource_ids_in(text)
    identifiers = frozenset(identifiers_from_text(text))
    if not resources and not identifiers:
        ctx.report.account("text_without_resource_or_identifier", len(chunk))
        return
    ctx.report.units_used += len(chunk)
    for resource_id in resources or [None]:
        ctx.add_record(
            location=location, system_id=None, resource_id=resource_id, collection=None,
            state=MENTIONED, state_basis=f"{extraction}_mention", observed_at=when, time_basis=basis,
            identifiers=identifiers,
            related_resources=frozenset(r for r in resources if r != resource_id),
            extraction=extraction, fields={"text": text},
        )


def parse_pdf(ctx: ParseContext) -> None:
    report = ctx.report
    report.parser = "pdf_text:pypdf"
    try:
        from pypdf import PdfReader
    except ImportError:
        report.errors.append("pypdf is not installed")
        return
    try:
        pages = [page.extract_text(extraction_mode="layout") or "" for page in PdfReader(str(ctx.path)).pages]
    except Exception as exc:  # pypdf raises many types for damaged files
        report.errors.append(f"could not read PDF: {exc}")
        return
    if not any(page.strip() for page in pages):
        report.errors.append("PDF has no extractable text (OCR not attempted)")
        return
    when, basis = _manifest_time(ctx)
    for page_number, page in enumerate(pages, start=1):
        block: list[str] = []
        block_start = 0
        lines = [line.strip() for line in page.splitlines()]
        for number, line in enumerate(lines, start=1):
            if not line:
                continue
            report.units_total += 1
            if "|" in line:
                _emit_mentions(ctx, [line], f"page {page_number} line {number}", "pdf_text", when, basis)
                continue
            if line.startswith("Record ") and block:
                _emit_mentions(ctx, block, f"page {page_number} lines {block_start}-{number - 1}", "pdf_text", when, basis)
                block = []
            if not block:
                block_start = number
            block.append(line)
        if block:
            _emit_mentions(ctx, block, f"page {page_number} lines {block_start}-{len(lines)}", "pdf_text", when, basis)


def tesseract_version() -> str | None:
    if shutil.which("tesseract") is None:
        return None
    result = subprocess.run(["tesseract", "--version"], capture_output=True, text=True, check=False)
    first = (result.stdout or result.stderr).splitlines()
    return first[0].strip() if first else None


def ocr_text(path: Path, cache_dir: Path | None) -> str:
    """Tesseract on a 2x grayscale upscale; cached by image hash, scale and engine version."""
    from PIL import Image

    version = tesseract_version() or "unknown"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    key = hashlib.sha256(f"{digest}:{OCR_SCALE}:{version}".encode()).hexdigest()[:32]
    if cache_dir is not None:
        cached = cache_dir / f"{key}.txt"
        if cached.exists():
            return cached.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as scratch:
        scaled = Path(scratch) / "scaled.png"
        image = Image.open(path).convert("L")
        image.resize((image.width * OCR_SCALE, image.height * OCR_SCALE), Image.LANCZOS).save(scaled)
        result = subprocess.run(["tesseract", str(scaled), "-"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"tesseract exited with {result.returncode}")
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{key}.txt").write_text(result.stdout, encoding="utf-8")
    return result.stdout


def parse_png(ctx: ParseContext, cache_dir: Path | None = None) -> None:
    report = ctx.report
    version = tesseract_version()
    report.parser = f"ocr:{version}" if version else "ocr"
    if version is None:
        report.errors.append("tesseract OCR is not installed; screenshot not parsed")
        return
    try:
        text = ocr_text(ctx.path, cache_dir)
    except Exception as exc:
        report.errors.append(f"OCR failed: {exc}")
        return
    when, basis = _manifest_time(ctx)
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        report.units_total += 1
        _emit_mentions(ctx, [line.strip()], f"ocr line {number}", "ocr", when, basis)
    if report.units_used == 0:
        report.warnings.append("OCR found no resource IDs or identifiers")
