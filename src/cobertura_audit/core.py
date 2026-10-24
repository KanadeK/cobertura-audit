"""Audit Cobertura line accounting without trusting declared aggregate rates."""

import posixpath
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from pathlib import Path

RATE_TOLERANCE = Decimal("0.0001")


class InputError(ValueError):
    """Input is not within the supported, bounded Cobertura XML profile."""


def _internal_subset(text):
    start = text.find("<!DOCTYPE")
    if start < 0:
        return False
    quote = None
    for char in text[start + len("<!DOCTYPE") :]:
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "[":
            return True
        elif char == ">":
            return False
    return False  # The XML parser will reject an unterminated declaration.


def parse_xml(path, *, max_bytes=32 * 1024 * 1024, max_nodes=250000):
    if type(max_bytes) is not int or max_bytes < 1 or type(max_nodes) is not int or max_nodes < 1:
        raise InputError("XML byte and node limits must be positive integers")
    with Path(path).open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise InputError("XML exceeds byte limit")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise InputError("only UTF-8 XML is supported") from exc
    if "\x00" in text or "<!ENTITY" in text or _internal_subset(text):
        raise InputError("XML entities and internal DTD subsets are forbidden")
    declaration = re.match(r"\s*<\?xml\b[^?]*encoding\s*=\s*['\"]([^'\"]+)", text)
    if declaration and declaration[1].lower() not in {"utf-8", "utf8"}:
        raise InputError("XML declaration must specify UTF-8")
    # No DTD is loaded and no external resource is fetched. Internal subsets
    # and entity declarations were rejected before feeding the XML parser.
    parser = ET.XMLPullParser(events=("start", "end"))  # noqa: S314
    count, root = 0, None
    try:
        for offset in range(0, len(text), 65536):
            parser.feed(text[offset : offset + 65536])
            for event, element in parser.read_events():
                if event == "start":
                    count += 1
                    if root is None:
                        root = element
                    if count > max_nodes:
                        raise InputError("XML exceeds node limit")
        parser.close()
    except ET.ParseError as exc:
        raise InputError("malformed XML") from exc
    if root is None or root.tag != "coverage":
        raise InputError("expected an unnamespaced Cobertura coverage root")
    return root


def _integer(value, *, positive=False):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,12}", value):
        raise ValueError("invalid count")
    number = int(value)
    if positive and number == 0:
        raise ValueError("invalid line number")
    return number


def _rate(value):
    try:
        rate = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid rate") from exc
    if not rate.is_finite() or not 0 <= rate <= 1:
        raise ValueError("rate outside zero to one")
    return rate


def _one(element, tag):
    children = element.findall(tag)
    if len(children) != 1:
        raise InputError(f"expected one {tag} element in supported structure")
    return children[0]


class Findings:
    def __init__(self, limit):
        self.limit, self.total, self.counts, self.items = limit, 0, {}, []

    def add(self, code, scope, **details):
        self.total += 1
        self.counts[code] = self.counts.get(code, 0) + 1
        if len(self.items) < self.limit:
            self.items.append({"code": code, "scope": scope, **details})


def _summary(element, observations, scope, findings, *, root=False):
    valid = len(observations)
    covered = sum(observations.values())
    ratio = Decimal(covered) / valid if valid else Decimal(0)
    for attribute, expected in [("lines-valid", valid), ("lines-covered", covered)]:
        value = element.get(attribute)
        if value is None:
            if root:
                findings.add("missing_summary", scope, attribute=attribute)
            continue
        try:
            actual = _integer(value)
        except ValueError:
            findings.add("invalid_summary_count", scope, attribute=attribute)
            continue
        if actual != expected:
            findings.add(
                "summary_count_mismatch",
                scope,
                attribute=attribute,
                declared=actual,
                computed=expected,
            )
    value = element.get("line-rate")
    if value is None:
        findings.add("missing_summary", scope, attribute="line-rate")
    else:
        try:
            actual = _rate(value)
        except ValueError:
            findings.add("invalid_summary_rate", scope)
        else:
            # Empty class/package rates vary by producer (0 or 1). They carry
            # no measurable evidence; the root still fails if entirely empty.
            if valid and abs(actual - ratio) > RATE_TOLERANCE:
                findings.add(
                    "summary_rate_mismatch", scope, declared=str(actual), computed=f"{ratio:.8f}"
                )
    return {
        "lines_valid": valid,
        "lines_covered": covered,
        "line_rate": f"{ratio:.8f}" if valid else None,
    }


def audit(root, *, min_line_rate="0", finding_limit=100, file_limit=50):
    """Recompute unique (normalized filename, line) observations and check summaries.

    Overlaps across classes fail even if hit counts agree: a multi-target merge
    cannot be safely interpreted as a single source coverage measurement.
    """
    try:
        minimum = _rate(min_line_rate)
    except ValueError as exc:
        raise InputError("minimum line rate must be a finite ratio from 0 to 1") from exc
    if any(type(n) is not int or n < 0 for n in (finding_limit, file_limit)):
        raise InputError("report limits must be nonnegative integers")
    if root.tag != "coverage":
        raise InputError("expected a Cobertura coverage root")
    findings = Findings(finding_limit)
    global_lines, file_lines = {}, {}
    class_count = 0
    for pi, package in enumerate(_one(root, "packages").findall("package")):
        package_lines = {}
        for ci, cls in enumerate(_one(package, "classes").findall("class")):
            class_count += 1
            scope = f"package[{pi}]/class[{ci}]"
            filename = cls.get("filename", "")
            if not filename or "\x00" in filename:
                raise InputError("every class requires a nonempty filename")
            filename = posixpath.normpath(filename.replace("\\", "/"))
            observations = {}
            for li, line in enumerate(_one(cls, "lines").findall("line")):
                try:
                    number = _integer(line.get("number"), positive=True)
                    hits = _integer(line.get("hits"))
                except ValueError:
                    findings.add("invalid_line_observation", f"{scope}/line[{li}]")
                    continue
                hit = hits > 0
                if number in observations:
                    findings.add("duplicate_class_line", scope, line=number)
                    observations[number] = observations[number] or hit
                else:
                    observations[number] = hit
            _summary(cls, observations, scope, findings)
            for number, hit in observations.items():
                key = (filename, number)
                if key in global_lines:
                    findings.add("overlapping_source_line", scope, filename=filename, line=number)
                global_lines[key] = global_lines.get(key, False) or hit
                package_lines[key] = package_lines.get(key, False) or hit
                file_lines.setdefault(filename, {})[number] = global_lines[key]
        _summary(package, package_lines, f"package[{pi}]", findings)
    totals = _summary(root, global_lines, "coverage", findings, root=True)
    valid, covered = totals["lines_valid"], totals["lines_covered"]
    if not valid:
        findings.add("no_measurable_lines", "coverage")
    elif Decimal(covered) < minimum * valid:
        findings.add(
            "below_threshold", "coverage", minimum=str(minimum), computed=totals["line_rate"]
        )
    files = []
    for filename, lines in sorted(file_lines.items())[:file_limit]:
        files.append(
            {"filename": filename, "lines_valid": len(lines), "lines_covered": sum(lines.values())}
        )
    return {
        "schema_version": 1,
        "passed": findings.total == 0,
        "computed": totals,
        "minimum_line_rate": str(minimum),
        "classes_checked": class_count,
        "files_checked": len(file_lines),
        "findings_count": findings.total,
        "codes": dict(sorted(findings.counts.items())),
        "findings": findings.items,
        "findings_truncated": findings.total > len(findings.items),
        "files": files,
        "files_truncated": len(file_lines) > len(files),
        "scope": "class-level line observations; unique normalized filename and line",
    }


def audit_file(path, *, max_bytes=32 * 1024 * 1024, max_nodes=250000, **options):
    return audit(parse_xml(path, max_bytes=max_bytes, max_nodes=max_nodes), **options)
