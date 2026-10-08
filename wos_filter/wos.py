from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from .models import DuplicateEntry, ParsedWos, WosRecord


FIELD_RE = re.compile(r"^([A-Z0-9]{2})(?: |$)(.*)$")


class WosParseError(ValueError):
    pass


def read_text_safely(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    candidates = ("utf-8-sig", "gb18030", "cp1252")
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_wos_file(path: str | Path) -> ParsedWos:
    source = str(Path(path).resolve())
    text = read_text_safely(source).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    records: list[WosRecord] = []
    warnings: list[str] = []
    header_lines: list[str] = []
    block: list[str] = []
    in_record = False
    first_record_seen = False

    for line_no, line in enumerate(lines, start=1):
        if not in_record and line.startswith("PT "):
            in_record = True
            first_record_seen = True
            block = [line]
            continue

        if in_record:
            block.append(line)
            if line == "ER" or line.startswith("ER "):
                record = _parse_record_block(block, source, len(records) + 1)
                records.append(record)
                block = []
                in_record = False
            continue

        if not first_record_seen and not line.startswith("EF"):
            header_lines.append(line)

    if block:
        warnings.append(f"{Path(source).name}：最后一条记录缺少 ER，已按现有内容读取。")
        records.append(_parse_record_block(block, source, len(records) + 1))

    if not records:
        raise WosParseError(f"{Path(source).name} 中没有找到以 PT 开始的 WOS 记录。")

    if not any(record.text("CR") for record in records):
        warnings.append(f"{Path(source).name}：没有检测到 CR 引用字段，CiteSpace 引用分析会受限。")
    missing_titles = sum(not record.title for record in records)
    if missing_titles:
        warnings.append(f"{Path(source).name}：有 {missing_titles} 条记录缺少题名 TI。")

    header = "\n".join(header_lines).strip()
    return ParsedWos(records=records, header=header, warnings=warnings)


def parse_many(paths: list[str]) -> ParsedWos:
    all_records: list[WosRecord] = []
    warnings: list[str] = []
    header = ""
    for path in paths:
        parsed = parse_wos_file(path)
        if not header and parsed.header:
            header = parsed.header
        all_records.extend(parsed.records)
        warnings.extend(parsed.warnings)
    if not header:
        header = "FN Clarivate Web of Science\nVR 1.0"
    return ParsedWos(records=all_records, header=header, warnings=warnings)


def _parse_record_block(lines: list[str], source_file: str, index: int) -> WosRecord:
    fields: dict[str, list[str]] = {}
    current_tag = ""
    for line in lines:
        match = FIELD_RE.match(line)
        if match:
            tag, value = match.groups()
            if tag in {"ER", "EF"}:
                current_tag = ""
                continue
            fields.setdefault(tag, []).append(value.strip())
            current_tag = tag
        elif line.startswith("   ") and current_tag:
            continuation = line[3:].strip()
            if current_tag in {"TI", "SO", "AB", "C1", "C3", "RP", "FX", "FU"} and fields[current_tag]:
                fields[current_tag][-1] = (fields[current_tag][-1] + " " + continuation).strip()
            else:
                fields[current_tag].append(continuation)
        elif line.strip() and current_tag:
            fields[current_tag].append(line.strip())
    raw_block = "\n".join(lines).strip("\n")
    if not raw_block.rstrip().endswith("ER"):
        raw_block = raw_block.rstrip() + "\nER"
    return WosRecord(fields=fields, raw_block=raw_block, source_file=source_file, source_index=index)


def normalize_doi(value: str) -> str:
    doi = value.strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:", "doi "):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi.strip().rstrip(".")


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(ch for ch in normalized if ch.isalnum())


def deduplicate(records: list[WosRecord]) -> tuple[list[WosRecord], list[DuplicateEntry]]:
    unique: list[WosRecord] = []
    logs: list[DuplicateEntry] = []
    aliases: dict[tuple[str, str], int] = {}

    for record in records:
        keys = _record_keys(record)
        matched_index = None
        matched_keys: list[tuple[str, str]] = []
        for key in keys:
            if key not in aliases:
                continue
            candidate = unique[aliases[key]]
            if key[0] == "TI+PY":
                candidate_doi = normalize_doi(candidate.text("DI"))
                incoming_doi = normalize_doi(record.text("DI"))
                if candidate_doi and incoming_doi and candidate_doi != incoming_doi:
                    continue
                first_a = _first_author(candidate)
                first_b = _first_author(record)
                if first_a and first_b and first_a != first_b:
                    continue
            matched_index = aliases[key]
            matched_keys.append(key)
            break
        if matched_index is None:
            matched_index = len(unique)
            unique.append(record)
            for key in keys:
                aliases.setdefault(key, matched_index)
            continue

        existing = unique[matched_index]
        reasons = [kind for kind, _ in matched_keys]
        reason = "+".join(dict.fromkeys(reasons)) or "标识符重复"
        if record.completeness_score() > existing.completeness_score():
            unique[matched_index] = record
            logs.append(DuplicateEntry(
                duplicate_record_id=existing.record_id,
                kept_record_id=record.record_id,
                reason=reason,
                duplicate_source=existing.source_file,
                kept_source=record.source_file,
            ))
            for key in _record_keys(existing) + keys:
                aliases.setdefault(key, matched_index)
        else:
            logs.append(DuplicateEntry(
                duplicate_record_id=record.record_id,
                kept_record_id=existing.record_id,
                reason=reason,
                duplicate_source=record.source_file,
                kept_source=existing.source_file,
            ))
            for key in keys:
                aliases.setdefault(key, matched_index)
    return unique, logs


def _record_keys(record: WosRecord) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    ut = record.text("UT").strip().upper()
    doi = normalize_doi(record.text("DI"))
    title = normalize_title(record.title)
    year = record.text("PY").strip()
    if ut:
        keys.append(("UT", ut))
    if doi:
        keys.append(("DOI", doi))
    if title and year:
        keys.append(("TI+PY", f"{title}|{year}"))
    return keys


def _first_author(record: WosRecord) -> str:
    author = next(iter(record.values("AU")), "")
    surname = author.split(",", 1)[0].split(" ", 1)[0]
    return normalize_title(surname)


def find_doi_conflicts(records: list[WosRecord]) -> list[dict[str, object]]:
    """Report same-title/year candidates with different DOIs; never remove them."""
    by_title_year: dict[tuple[str, str], list[WosRecord]] = defaultdict(list)
    for record in records:
        title = normalize_title(record.title)
        year = record.text("PY").strip()
        if title and year and normalize_doi(record.text("DI")):
            by_title_year[(title, year)].append(record)
    return [
        {"title": items[0].title, "year": year,
         "record_ids": [item.record_id for item in items],
         "dois": sorted({normalize_doi(item.text("DI")) for item in items})}
        for (_, year), items in by_title_year.items()
        if len({normalize_doi(item.text("DI")) for item in items}) > 1
    ]


def build_citespace_text(header: str, records: list[WosRecord]) -> str:
    """Write a consistent Web of Science plain-text interchange file.

    Imported formats and edited records have different raw blocks; fields are
    the authoritative values.  WoS list fields use a tagged first item and
    three-space continuations, as in a native full-record export.
    """
    lines = ["FN Clarivate Analytics Web of Science", "VR 1.0"]
    for record in records:
        lines.extend(_export_record_lines(record))
        lines.append("")
    lines.append("EF")
    return "\n".join(lines) + "\n"


_EXPORT_ORDER = (
    "PT", "AU", "AF", "TI", "SO", "LA", "DT", "DE", "ID", "AB",
    "C1", "C3", "RP", "EM", "CR", "NR", "TC", "Z9", "U1", "U2",
    "PU", "PI", "PA", "SN", "EI", "J9", "JI", "PD", "PY", "VL",
    "IS", "BP", "EP", "AR", "DI", "UT", "WC", "SC", "CO",
)
_CONTINUATION_TAGS = {"AU", "AF", "BA", "BE", "BF", "CA", "GP", "CR", "RI", "OI"}
_SEMICOLON_TAGS = {"DE", "ID", "WC", "SC"}


def _export_record_lines(record: WosRecord) -> list[str]:
    fields = record.fields
    ordered = [tag for tag in _EXPORT_ORDER if tag in fields]
    ordered.extend(tag for tag in fields if tag not in ordered and tag not in {"ER", "EF"})
    if "PT" not in fields:
        ordered.insert(0, "PT")
    lines: list[str] = []
    for tag in ordered:
        values = record.values(tag) if tag != "PT" else (record.values("PT") or ["J"])
        values = [value.replace("\r", " ").replace("\n", " ") for value in values if value.strip()]
        if not values:
            continue
        if tag in _SEMICOLON_TAGS:
            lines.append(f"{tag} {'; '.join(value.rstrip('; ') for value in values)}")
        elif tag in _CONTINUATION_TAGS:
            lines.append(f"{tag} {values[0]}")
            lines.extend(f"   {value}" for value in values[1:])
        else:
            lines.extend(f"{tag} {value}" for value in values)
    lines.append("ER")
    return lines
