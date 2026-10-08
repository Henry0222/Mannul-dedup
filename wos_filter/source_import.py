"""Detect bibliographic exports by content and adapt them to the screening model.

The original WoS text parser remains the authority for tagged WoS exports. Other
formats are mapped to the fields used by the existing screening and export flow.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from ._vendor import xlrd
from .models import ParsedWos, WosRecord
from .wos import parse_wos_file, read_text_safely


class ImportFormatError(ValueError):
    pass


@dataclass(frozen=True)
class ImportFileSummary:
    path: str
    source: str
    format: str
    count: int


SUPPORTED_SUFFIXES = frozenset({".txt", ".ris", ".bib", ".csv", ".xls", ".nbib"})
_RIS = re.compile(r"^([A-Z0-9]{2})\s+-\s?(.*)$")
_NBIB = re.compile(r"^([A-Z0-9]{2,4})\s*-\s?(.*)$")
_REFWORKS = re.compile(r"^([A-Z][A-Z0-9]|vo)\s+(.+)$")
_DOI = re.compile(r"10\.\d{4,9}/[^\s\[\]<>;,]+", re.IGNORECASE)
_YEAR = re.compile(r"\b(?:18|19|20|21)\d{2}\b")


def _year(value: str) -> str:
    match = _YEAR.search(value)
    return match.group() if match else ""


def _doi(value: str) -> str:
    match = _DOI.search(value)
    return match.group().rstrip(".、；") if match else ""


def _values(fields: dict[str, list[str]], *names: str) -> list[str]:
    for name in names:
        values = [value.strip() for value in fields.get(name, []) if value.strip()]
        if values:
            return values
    return []


def _first(fields: dict[str, list[str]], *names: str) -> str:
    return next(iter(_values(fields, *names)), "")


def _split_semicolons(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;；]", value) if part.strip()]


def _add(fields: dict[str, list[str]], tag: str, value: str | list[str]) -> None:
    values = value if isinstance(value, list) else [value]
    clean = [part.strip() for part in values if part and part.strip()]
    if clean:
        fields[tag] = clean


def _synthetic_record(fields: dict[str, list[str]], path: Path, index: int,
                      source: str, format_id: str) -> WosRecord:
    if not _first(fields, "TI"):
        raise ImportFormatError(f"{path.name} 第 {index} 条缺少题名，无法进入文献筛选。")
    lines = ["PT " + (_first(fields, "PT") or "J")]
    for tag, values in fields.items():
        if tag == "PT":
            continue
        for value in values:
            lines.append(f"{tag} {value.replace(chr(10), ' ').replace(chr(13), ' ')}")
    lines.append("ER")
    return WosRecord(fields=fields, raw_block="\n".join(lines),
                     source_file=str(path.resolve()), source_index=index,
                     source_kind=source, source_format=format_id)


def _table_record(row: dict[str, str], path: Path, index: int, source: str,
                  format_id: str, mapping: dict[str, str]) -> WosRecord:
    fields: dict[str, list[str]] = {}
    for header, tag in mapping.items():
        value = (row.get(header) or "").strip()
        if value:
            _add(fields, tag, _split_semicolons(value) if tag in {"AU", "AF", "DE", "ID", "CR", "C1", "WC", "SC"} else value)
    _add(fields, "DI", _doi(_first(fields, "DI")))
    _add(fields, "PY", _year(_first(fields, "PY")))
    return _synthetic_record(fields, path, index, source, format_id)


_WOS_CODE_MAP = {key: key for key in (
    "PT", "AU", "AF", "TI", "SO", "LA", "DT", "DE", "ID", "AB", "C1", "C3",
    "RP", "FU", "FX", "CR", "NR", "TC", "Z9", "SN", "EI", "PY", "VL",
    "IS", "BP", "EP", "AR", "DI", "WC", "SC", "UT", "PM",
)}
_WOS_XLS_MAP = {
    "Publication Type": "PT", "Authors": "AU", "Author Full Names": "AF",
    "Article Title": "TI", "Source Title": "SO", "Language": "LA",
    "Document Type": "DT", "Author Keywords": "DE", "Keywords Plus": "ID",
    "Abstract": "AB", "Addresses": "C1", "Affiliations": "C3",
    "Reprint Addresses": "RP", "Funding Orgs": "FU", "Funding Text": "FX",
    "Cited References": "CR", "Cited Reference Count": "NR",
    "Times Cited, WoS Core": "TC", "Times Cited, All Databases": "Z9",
    "ISSN": "SN", "eISSN": "EI", "Publication Year": "PY",
    "Volume": "VL", "Issue": "IS", "Start Page": "BP", "End Page": "EP",
    "Article Number": "AR", "DOI": "DI", "WoS Categories": "WC",
    "Research Areas": "SC", "UT (Unique WOS ID)": "UT", "Pubmed Id": "PM",
}


def _read_wos_table(path: Path, text: str | None, is_xls: bool) -> list[WosRecord]:
    if is_xls:
        book = None
        try:
            book = xlrd.open_workbook(str(path), on_demand=True)
            sheet = book.sheet_by_index(0)
            headers = [str(value).strip() for value in sheet.row_values(0)]
            if "Article Title" not in headers or "UT (Unique WOS ID)" not in headers:
                raise ImportFormatError(f"{path.name} 不是可识别的 WoS XLS 导出。")
            rows = (
                {header: str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
                 for header, value in zip(headers, sheet.row_values(index)) if header and value not in ("", None)}
                for index in range(1, sheet.nrows)
            )
            return [_table_record(row, path, index, "wos", "wos_xls", _WOS_XLS_MAP)
                    for index, row in enumerate(rows, 1) if row.get("Article Title", "").strip()]
        except (IndexError, xlrd.XLRDError) as error:
            raise ImportFormatError(f"{path.name} 的 XLS 表格无法读取：{error}") from error
        finally:
            if book is not None:
                book.release_resources()
    assert text is not None
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    headers = reader.fieldnames or []
    if not {"PT", "TI", "UT"}.issubset(headers):
        raise ImportFormatError(f"{path.name} 不是可识别的 WoS 表格文本导出。")
    return [_table_record(row, path, index, "wos", "wos_tsv", _WOS_CODE_MAP)
            for index, row in enumerate(reader, 1) if row.get("TI", "").strip()]


def _parse_tagged(text: str, kind: str) -> list[dict[str, list[str]]]:
    pattern = _NBIB if kind == "pubmed_nbib" else _RIS
    start_tag = "PMID" if kind == "pubmed_nbib" else "TY"
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    previous = ""
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            tag, value = match.groups()
            if tag == start_tag and current:
                records.append(current)
                current = {}
            if tag == "ER":
                if current:
                    records.append(current)
                current, previous = {}, ""
                continue
            current.setdefault(tag, []).append(value.strip())
            previous = tag
        elif line.strip() and previous and current:
            current[previous][-1] += " " + line.strip()
    if current:
        records.append(current)
    return records


def _parse_refworks(text: str) -> list[dict[str, list[str]]]:
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    previous = ""
    for line in text.splitlines():
        match = _REFWORKS.match(line)
        if match:
            tag, value = match.groups()
            if tag == "RT" and current:
                records.append(current)
                current = {}
            current.setdefault(tag, []).append(value.strip())
            previous = tag
        elif line.strip() and previous and current:
            current[previous][-1] += " " + line.strip()
    if current:
        records.append(current)
    return records


def _ris_record(item: dict[str, list[str]], path: Path, index: int, source: str,
                kind: str) -> WosRecord:
    fields: dict[str, list[str]] = {}
    if kind == "pubmed_nbib":
        mapping = {"TI": ("TI",), "AU": ("AU", "FAU"), "SO": ("JT", "TA"),
                   "AB": ("AB",), "DE": ("OT",), "ID": ("MH",), "C1": ("AD",),
                   "VL": ("VI",), "IS": ("IP",), "BP": ("PG",), "DT": ("PT",)}
        for target, names in mapping.items():
            _add(fields, target, _values(item, *names))
        _add(fields, "PY", _year(_first(item, "DP", "DEP")))
        doi_candidates = _values(item, "AID") + _values(item, "LID")
        _add(fields, "DI", next((_doi(value) for value in doi_candidates if "[doi]" in value.casefold() and _doi(value)), ""))
        pmid = _first(item, "PMID")
        if pmid:
            _add(fields, "UT", "PUBMED:" + pmid)
    elif kind == "refworks":
        mapping = {"TI": ("T1",), "AU": ("A1",), "SO": ("JF", "JO", "T2"),
                   "AB": ("AB",), "DE": ("K1",), "C1": ("AD", "C1"),
                   "VL": ("VO", "vo"), "IS": ("IS",), "BP": ("SP", "BP"),
                   "EP": ("OP", "EP"), "DT": ("RT",)}
        for target, names in mapping.items():
            values = _values(item, *names)
            _add(fields, target, [part for value in values for part in _split_semicolons(value)] if target in {"AU", "DE"} else values)
        _add(fields, "PY", _year(_first(item, "YR", "FD")))
        _add(fields, "DI", _doi("; ".join(_values(item, "DO"))))
    else:
        mapping = {"TI": ("TI", "T1"), "AU": ("AU", "A1"),
                   "SO": ("JO", "JF", "T2", "JA"), "AB": ("AB", "N2"),
                   "DE": ("KW",), "C1": ("AD",), "VL": ("VL",),
                   "IS": ("IS",), "BP": ("SP",), "EP": ("EP",),
                   "DT": ("TY",), "LA": ("LA",)}
        for target, names in mapping.items():
            values = _values(item, *names)
            _add(fields, target, [part for value in values for part in _split_semicolons(value)] if target == "DE" else values)
        _add(fields, "PY", _year(_first(item, "PY", "Y1", "DA")))
        _add(fields, "DI", _doi(_first(item, "DO")))
        identifier = _first(item, "AN")
        if identifier.startswith("WOS:"):
            _add(fields, "UT", identifier)
        elif source == "scopus":
            match = re.search(r"/publications/(\d+)", _first(item, "UR"))
            if match:
                _add(fields, "UT", "SCOPUS:" + match.group(1))
    notes = " ".join(item.get("N1", []))
    if source == "wos":
        core = re.search(r"Times Cited in Web of Science Core Collection\s*:\s*([\d,]+)", notes, re.I)
        total = re.search(r"Total Times Cited\s*:\s*([\d,]+)", notes, re.I)
        if core:
            _add(fields, "TC", core.group(1).replace(",", ""))
        if total:
            _add(fields, "Z9", total.group(1).replace(",", ""))
    elif source == "scopus":
        cited = re.search(r"Cited By\s*:\s*([\d,]+)", notes, re.I)
        if cited:
            _add(fields, "TC", cited.group(1).replace(",", ""))
    return _synthetic_record(fields, path, index, source, kind)


def _bib_entries(text: str) -> list[tuple[str, dict[str, str]]]:
    entries = []
    start = re.compile(r"@([A-Za-z]+)\s*[{(]")
    position = 0
    while match := start.search(text, position):
        kind = match.group(1).casefold()
        position = match.end()
        key_end = text.find(",", position)
        if key_end < 0:
            break
        citekey = text[position:key_end].strip()
        position = key_end + 1
        fields: dict[str, str] = {}
        while position < len(text):
            while position < len(text) and text[position] in " \t\r\n,":
                position += 1
            if position >= len(text) or text[position] in "})":
                position += 1
                break
            key_match = re.match(r"[A-Za-z][\w-]*", text[position:])
            if not key_match:
                raise ImportFormatError(f"BibTeX 字段格式不完整：{citekey}")
            name = key_match.group().casefold()
            position += len(key_match.group())
            while position < len(text) and text[position].isspace():
                position += 1
            if position >= len(text) or text[position] != "=":
                raise ImportFormatError(f"BibTeX 字段缺少等号：{citekey}/{name}")
            position += 1
            while position < len(text) and text[position].isspace():
                position += 1
            if position >= len(text):
                raise ImportFormatError(f"BibTeX 字段缺少值：{citekey}/{name}")
            opening = text[position]
            if opening in '{"':
                closing = "}" if opening == "{" else '"'
                depth = 1
                position += 1
                begin = position
                while position < len(text) and depth:
                    char = text[position]
                    if char == "\\" and position + 1 < len(text):
                        position += 2
                        continue
                    if opening == "{" and char == "{":
                        depth += 1
                    elif char == closing:
                        depth -= 1
                    position += 1
                if depth:
                    raise ImportFormatError(f"BibTeX 花括号未闭合：{citekey}/{name}")
                value = text[begin:position - 1]
            else:
                begin = position
                while position < len(text) and text[position] not in ",}\r\n":
                    position += 1
                value = text[begin:position]
            fields[name] = re.sub(r"\s+", " ", value.replace("\\&", "&")).strip()
        if kind not in {"comment", "string", "preamble"}:
            entries.append((citekey, fields))
    return entries


def _bib_record(citekey: str, item: dict[str, str], path: Path, index: int,
                source: str) -> WosRecord:
    fields: dict[str, list[str]] = {}
    mapping = {"TI": ("title",), "SO": ("journal", "booktitle"),
               "AB": ("abstract",), "ID": ("keywords-plus",),
               "C1": ("affiliations", "affiliation"), "VL": ("volume",),
               "IS": ("number",), "BP": ("pages",), "DT": ("type",),
               "LA": ("language",), "WC": ("web-of-science-categories",),
               "CR": ("cited-references",)}
    for target, names in mapping.items():
        _add(fields, target, next((item[name] for name in names if item.get(name)), ""))
    _add(fields, "AU", [part.strip() for part in re.split(r"\s+and\s+", item.get("author", ""), flags=re.IGNORECASE) if part.strip()])
    _add(fields, "DE", _split_semicolons(item.get("author_keywords") or item.get("keywords", "")))
    if source == "scopus":
        _add(fields, "ID", _split_semicolons(item.get("keywords", "")))
        _add(fields, "C1", item.get("affiliations", ""))
    _add(fields, "PY", _year(item.get("year", "")))
    _add(fields, "DI", _doi(item.get("doi", "")))
    citation = next((item[name] for name in ("citedby", "cited-by", "citationcount", "times-cited")
                     if item.get(name)), "")
    if re.fullmatch(r"[\d,]+", citation.strip()):
        _add(fields, "TC", citation.replace(",", ""))
    if source == "wos":
        _add(fields, "UT", item.get("unique-id", citekey if citekey.upper().startswith("WOS:") else ""))
    else:
        match = re.search(r"/publications/(\d+)", item.get("url", ""))
        if match:
            _add(fields, "UT", "SCOPUS:" + match.group(1))
    return _synthetic_record(fields, path, index, source, "bibtex")


def _scopus_plain(text: str, path: Path) -> list[WosRecord]:
    blocks = []
    current = []
    for line in text.splitlines()[2:]:
        current.append(line)
        if line.startswith("EID: "):
            blocks.append(current)
            current = []
    records = []
    for index, block in enumerate(blocks, 1):
        lines = [line.strip() for line in block if line.strip()]
        citation = next((i for i, line in enumerate(lines) if re.match(r"^\(\d{4}\)\s", line)), -1)
        if citation < 1:
            raise ImportFormatError(f"{path.name} 第 {index} 条 Scopus 文本缺少题名或引文行。")
        fields: dict[str, list[str]] = {}
        _add(fields, "TI", lines[citation - 1])
        _add(fields, "AU", lines[0])
        year_match = re.match(r"^\((\d{4})\)\s*(.*)$", lines[citation])
        if year_match:
            _add(fields, "PY", year_match.group(1))
            _add(fields, "SO", re.split(r",\s*(?:\d|art\.)", year_match.group(2), maxsplit=1)[0])
        tags = {key: next((line[len(key) + 2:].strip() for line in lines if line.startswith(key + ": ")), "")
                for key in ("DOI", "ABSTRACT", "AUTHOR KEYWORDS", "INDEX KEYWORDS",
                            "AFFILIATIONS", "DOCUMENT TYPE", "EID", "CITED BY")}
        _add(fields, "DI", _doi(tags["DOI"]))
        _add(fields, "AB", tags["ABSTRACT"])
        _add(fields, "DE", _split_semicolons(tags["AUTHOR KEYWORDS"]))
        _add(fields, "ID", _split_semicolons(tags["INDEX KEYWORDS"]))
        _add(fields, "C1", tags["AFFILIATIONS"])
        _add(fields, "DT", tags["DOCUMENT TYPE"])
        _add(fields, "UT", "SCOPUS:" + tags["EID"])
        if re.fullmatch(r"[\d,]+", tags["CITED BY"]):
            _add(fields, "TC", tags["CITED BY"].replace(",", ""))
        records.append(_synthetic_record(fields, path, index, "scopus", "scopus_plain"))
    return records


def _csv_records(text: str, path: Path, source: str) -> list[WosRecord]:
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if source == "pubmed":
        mapping = {"Title": "TI", "Authors": "AU", "Journal/Book": "SO",
                   "Publication Year": "PY", "DOI": "DI"}
        records = []
        for index, row in enumerate(rows, 1):
            record = _table_record(row, path, index, source, "pubmed_csv", mapping)
            pmid = (row.get("PMID") or "").strip()
            if pmid:
                _add(record.fields, "UT", "PUBMED:" + pmid)
            record.raw_block = _synthetic_record(record.fields, path, index, source, "pubmed_csv").raw_block
            records.append(record)
        return records
    mapping = {"Title": "TI", "Authors": "AU", "Year": "PY", "Source title": "SO",
               "Author Keywords": "DE", "Index Keywords": "ID", "Abstract": "AB",
               "Authors with affiliations": "C1", "DOI": "DI", "EID": "UT",
               "Document Type": "DT", "Volume": "VL", "Issue": "IS", "Cited by": "TC"}
    records = []
    for index, row in enumerate(rows, 1):
        mapped = _table_record(row, path, index, source, "scopus_csv", mapping)
        if mapped.fields.get("UT"):
            mapped.fields["UT"][0] = "SCOPUS:" + mapped.fields["UT"][0]
            mapped.raw_block = _synthetic_record(mapped.fields, path, index, source, "scopus_csv").raw_block
        records.append(mapped)
    return records


def _detect_text(text: str, path: Path) -> tuple[str, str]:
    head = text.lstrip("\ufeff\r\n \t")[:4096]
    if re.search(r"(?m)^FN\s+Clarivate", head) or ("\nVR " in head and "\nPT " in head):
        return "wos", "wos_tagged"
    if re.search(r"(?m)^PMID\s*-\s*\d+", head):
        return "pubmed", "pubmed_nbib"
    if re.search(r"(?m)^TY\s+-\s*", head):
        sample = text[:20000].casefold()
        if "sciencedirect.com/" in sample:
            return "sciencedirect", "ris"
        if re.search(r"(?m)^DB\s+-\s*Scopus", text[:20000], re.IGNORECASE) or "scopus.com/" in sample:
            return "scopus", "ris"
        if re.search(r"(?m)^AN\s+-\s*WOS:", text[:20000], re.IGNORECASE):
            return "wos", "ris"
        return "ris", "ris"
    if re.search(r"(?m)^RT\s+\S", head):
        match = re.search(r"(?m)^DS\s+(.+)$", text[:20000])
        source = match.group(1).strip().casefold() if match else "refworks"
        aliases = {"cnki": "cnki", "知网": "cnki", "wanfang": "wanfang", "万方": "wanfang",
                   "cqvip": "cqvip", "维普": "cqvip", "yiigle": "yiigle", "中华医学库": "yiigle"}
        return aliases.get(source, "refworks"), "refworks"
    if head.startswith("Scopus\nEXPORT DATE:"):
        return "scopus", "bibtex" if re.search(r"(?m)^@\w+\s*[{(]", text[:20000]) else "scopus_plain"
    if re.search(r"(?m)^@\w+\s*[{(]", head):
        return ("wos" if "WOS:" in head or "Unique-ID" in text[:20000] else "bibtex"), "bibtex"
    first = text.splitlines()[0].lstrip("\ufeff") if text.splitlines() else ""
    if "\t" in first:
        headers = first.split("\t")
        if {"PT", "TI", "UT"}.issubset(headers):
            return "wos", "wos_tsv"
    if "," in first:
        headers = next(csv.reader([first]))
        if {"PMID", "Title"}.issubset(headers):
            return "pubmed", "pubmed_csv"
        if {"Title", "Authors"}.issubset(headers):
            return "scopus", "scopus_csv"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and all(re.fullmatch(r"\d{5,9}", line) for line in lines):
        return "pubmed", "pmid_list"
    raise ImportFormatError(f"{path.name} 的内容不属于已支持的题录格式。")


def import_one(path: str | Path) -> tuple[list[WosRecord], ImportFileSummary, list[str]]:
    path = Path(path)
    if path.suffix.casefold() not in SUPPORTED_SUFFIXES:
        raise ImportFormatError(f"{path.name} 的扩展名不在支持列表中。")
    data = path.read_bytes()
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        source, kind, text = "wos", "wos_xls", None
    else:
        text = read_text_safely(path).replace("\r\n", "\n").replace("\r", "\n")
        source, kind = _detect_text(text, path)
    warnings = []
    if kind == "wos_tagged":
        parsed = parse_wos_file(path)
        records, warnings = parsed.records, parsed.warnings
    elif kind in {"wos_tsv", "wos_xls"}:
        records = _read_wos_table(path, text, kind == "wos_xls")
    elif kind == "pubmed_nbib":
        records = [_ris_record(item, path, index, source, kind)
                   for index, item in enumerate(_parse_tagged(text, kind), 1)]
    elif kind == "ris":
        records = [_ris_record(item, path, index, source, kind)
                   for index, item in enumerate(_parse_tagged(text, kind), 1)]
    elif kind == "refworks":
        records = [_ris_record(item, path, index, source, kind)
                   for index, item in enumerate(_parse_refworks(text), 1)]
    elif kind == "bibtex":
        records = [_bib_record(key, item, path, index, source)
                   for index, (key, item) in enumerate(_bib_entries(text), 1)]
    elif kind == "scopus_plain":
        records = _scopus_plain(text, path)
    elif kind in {"pubmed_csv", "scopus_csv"}:
        records = _csv_records(text, path, source)
    elif kind == "pmid_list":
        records = []
        warnings.append(f"{path.name} 只有 PMID 编号，没有题名或摘要；已识别但不能单独进入筛选。请同时导入 PubMed 完整题录 TXT 或 CSV。")
    else:
        raise ImportFormatError(f"暂不支持：{path.name} ({kind})")
    if not records and kind != "pmid_list":
        raise ImportFormatError(f"{path.name} 已识别为 {kind}，但未读到有效题录。")
    return records, ImportFileSummary(str(path.resolve()), source, kind, len(records)), warnings


def parse_many_sources(paths: list[str]) -> ParsedWos:
    records: list[WosRecord] = []
    warnings: list[str] = []
    files: list[ImportFileSummary] = []
    header = ""
    for path in paths:
        imported, summary, notes = import_one(path)
        records.extend(imported)
        files.append(summary)
        warnings.extend(notes)
        if summary.format == "wos_tagged" and not header:
            header = parse_wos_file(path).header
    if not records:
        raise ImportFormatError("所选文件没有可筛选的完整题录；PMID 编号列表需配合完整 PubMed 导出。")
    return ParsedWos(records=records, header=header or "FN Clarivate Web of Science\nVR 1.0",
                     warnings=warnings, imports=files)
