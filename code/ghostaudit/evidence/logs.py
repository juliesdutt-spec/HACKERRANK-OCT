"""Operational logs in the six line syntaxes observed in dataset/evidence/logs.

Every line is classified, none is dropped silently:
* state lines carry a resource_id and are emitted as records;
* lookup lines (events ending in _started) carry the identifier being looked up; they are linked
  to state lines of the same trace_id, which is how "lookup_key=<email>" reaches the completion
  line that holds the resource_id and the result;
* operational lines (connection pools, retries, cache, heartbeats of the collector) carry no
  resource and are only counted.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .model import (
    PRESENT,
    ParseContext,
    derive_state,
    identifiers_from_fields,
    parse_time_or_none,
    related_resources_from_fields,
)

TS = r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)"
SYSLOG = re.compile(TS + r" <\d+>\d+ (\S+) (\S+) (\S+) - - (.*)$")
JSONLINE = re.compile(TS + r" (\{.*\})$")
BRACKET = re.compile(TS + r" \[(\w+)\] \[([^\]]+)\] \[([^\]]+)\] ([\w.]+): (.*)$")
PIPE = re.compile(TS + r" \| (\w+) \| (\S+) \| (\S+) \| ([\w.]+) \| (.*)$")
CLOUD = re.compile(TS + r' requestId=(\S+) level=(\w+) function=(\S+) message="(.*)"$')
KEYVALUE = re.compile(TS + r" (?:(INFO|DEBUG|WARN|ERROR) )?(\w+=.*)$")
PAIR = re.compile(r'(\w+)=("((?:[^"\\]|\\.)*)"|\S+)')

LOOKUP_KEYS = ("lookup_key", "match_key", "subject_key", "subject_ref", "scan_key", "owner_key")
READ_RESULT_SUFFIXES = ("_read_completed", "_read", "_loaded")


def _pairs(text: str) -> dict[str, str]:
    fields = {}
    for key, raw, quoted in PAIR.findall(text):
        fields[key] = quoted.replace('\\"', '"') if raw.startswith('"') else raw
    return fields


def parse_line(line: str) -> tuple[str, dict[str, Any], str] | None:
    """Return (timestamp, fields, syntax) or None when the line matches no observed syntax."""
    if match := SYSLOG.match(line):
        fields = _pairs(match[5])
        fields.setdefault("service", match[3])
        fields.setdefault("trace_id", match[4])
        return match[1], fields, "syslog"
    if match := JSONLINE.match(line):
        try:
            fields = json.loads(match[2])
        except json.JSONDecodeError:
            return None
        if not isinstance(fields, dict):
            return None
        fields.setdefault("service", fields.get("logger"))
        return match[1], {k: v for k, v in fields.items()}, "json"
    if match := BRACKET.match(line):
        fields = _pairs(match[6])
        fields.update(level=match[2], service=match[3], trace_id=match[4], event=match[5])
        return match[1], fields, "bracket"
    if match := PIPE.match(line):
        fields = _pairs(match[6])
        fields.update(level=match[2], service=match[3], trace_id=match[4], event=match[5])
        return match[1], fields, "pipe"
    if match := CLOUD.match(line):
        fields = _pairs(match[5].replace('\\"', '"'))
        fields.update(level=match[3], service=match[4], trace_id=match[2])
        return match[1], fields, "cloud"
    if match := KEYVALUE.match(line):
        fields = _pairs(match[3])
        if match[2]:
            fields.setdefault("level", match[2])
        return match[1], fields, "keyvalue"
    return None


def parse_log(ctx: ParseContext) -> None:
    report = ctx.report
    report.parser = "operational_log"
    lines = ctx.path.read_text(encoding="utf-8").splitlines()
    parsed: list[tuple[int, Any, dict[str, Any]]] = []
    syntaxes: dict[str, int] = defaultdict(int)
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            report.note("blank_line")
            continue
        report.units_total += 1
        result = parse_line(line)
        if result is None:
            report.warnings.append(f"line {number}: matches no known log syntax: {line[:120]!r}")
            report.account("unparsed_line")
            continue
        stamp, fields, syntax = result
        when = parse_time_or_none(stamp)
        if when is None:
            report.warnings.append(f"line {number}: unparseable timestamp {stamp!r}")
        syntaxes[syntax] += 1
        parsed.append((number, when, fields))
    report.parser = "operational_log:" + ",".join(sorted(syntaxes))

    # identifiers looked up per trace, with the line that carried them
    lookups: dict[str, list[tuple[int, set]]] = defaultdict(list)
    for number, _when, fields in parsed:
        if str(fields.get("event", "")).endswith("_started") and fields.get("trace_id"):
            keys = {k: fields[k] for k in LOOKUP_KEYS if k in fields}
            lookups[fields["trace_id"]].append((number, identifiers_from_fields(keys)))

    for number, when, fields in parsed:
        event = str(fields.get("event", ""))
        if event.endswith("_started"):
            report.account("lookup_line")
            continue
        resource_id = fields.get("resource_id")
        if not resource_id:
            report.account("operational_line")
            continue
        identifiers = identifiers_from_fields(fields)
        location = f"line {number}"
        trace = fields.get("trace_id")
        trace_lookups = [entry for entry in lookups.get(trace, []) if entry[0] < number] if trace else []
        distinct = {frozenset(ids) for _, ids in trace_lookups if ids}
        if len(distinct) == 1:
            identifiers |= set(next(iter(distinct)))
            location += f" (trace {trace}; lookup line {trace_lookups[-1][0]})"
        elif len(distinct) > 1:
            report.warnings.append(f"line {number}: trace {trace} looked up several identifiers; none attached")
        state, basis = derive_state(fields)
        if state is None and basis is None and event.endswith(READ_RESULT_SUFFIXES):
            # a completed read returns the record's fields, so the record existed when read
            state, basis = PRESENT, f"event={event}"
        if state is None and basis is not None:
            report.warnings.append(f"line {number}: unmapped state value {basis}")
        explicit = fields.get("system")
        ctx.add_record(
            location=location, system_id=explicit, resource_id=resource_id, collection=event or None,
            record_type=fields.get("record_type"), state=state, state_basis=basis,
            observed_at=when, time_basis="log_line" if when else "none",
            identifiers=frozenset(identifiers),
            related_resources=related_resources_from_fields(fields, resource_id), fields=fields,
        )
        report.units_used += 1
