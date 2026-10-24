"""Resolve a request email to a stable user and recover every identifier that user held.

The identity snapshot holds each user's current values. The audit log holds the
change history of email, email_hash and phone, so each field forms a chain:
the first event's old_value is the original value and every new_value follows it.
A user's identifiers "as of" a time are the chain values acquired by then.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from .data import parse_time

EPOCH = datetime.min.replace(tzinfo=timezone.utc)
HISTORY_FIELDS = ("email", "email_hash", "phone")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def email_hash(email: str) -> str:
    """The dataset's email_hash is the SHA-256 of the lower-cased address."""
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Identifiers:
    """Every identifier a user held at or before a point in time."""

    user_id: str
    name: str
    emails: frozenset[str]
    phones: frozenset[str]
    email_hashes: frozenset[str]


class IdentityIndex:
    def __init__(self, identities: list[dict[str, str]], audit_rows: list[dict[str, str]]):
        self._snapshot = {row["user_id"]: row for row in identities}
        events: dict[tuple[str, str], list[tuple[datetime, str, str, str]]] = defaultdict(list)
        for row in audit_rows:
            if row["field_name"] in HISTORY_FIELDS:
                events[(row["user_id"], row["field_name"])].append(
                    (parse_time(row["event_time"]), row["audit_id"], row["old_value"], row["new_value"])
                )
        # (user, field) -> [(acquired_at, value)] in chronological order
        self._chains: dict[tuple[str, str], list[tuple[datetime, str]]] = {}
        for user_id, row in self._snapshot.items():
            for field in HISTORY_FIELDS:
                self._chains[(user_id, field)] = self._build_chain(
                    row[field], sorted(events.get((user_id, field), []))
                )
        self._email_owners: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
        for (user_id, field), chain in self._chains.items():
            if field == "email":
                for acquired_at, value in chain:
                    self._email_owners[normalize_email(value)].append((acquired_at, user_id))

    @staticmethod
    def _build_chain(current: str, events: list) -> list[tuple[datetime, str]]:
        if not events:
            return [(EPOCH, current)]
        chain = [(EPOCH, events[0][2])]
        chain.extend((when, new_value) for when, _audit_id, _old, new_value in events)
        return chain

    def user_ids(self) -> list[str]:
        return sorted(self._snapshot)

    def resolve_email(self, email: str, audit_at: datetime | None = None) -> str | None:
        """Return the user who held this email at or before audit_at, or None if unknown.

        If several users held it, the most recent holder wins: an address released by one
        user and later taken by another belongs to the later holder.
        """
        holders = [
            (acquired_at, user_id)
            for acquired_at, user_id in self._email_owners.get(normalize_email(email), [])
            if audit_at is None or acquired_at <= audit_at
        ]
        if not holders:
            return None
        return max(holders, key=lambda holder: (holder[0], holder[1]))[1]

    def identifiers(self, user_id: str, audit_at: datetime | None = None) -> Identifiers:
        def held(field: str) -> list[str]:
            return [
                value
                for acquired_at, value in self._chains[(user_id, field)]
                if audit_at is None or acquired_at <= audit_at
            ]

        emails = frozenset(normalize_email(value) for value in held("email"))
        return Identifiers(
            user_id=user_id,
            name=self._snapshot[user_id]["name"],
            emails=emails,
            phones=frozenset(held("phone")),
            email_hashes=frozenset(held("email_hash")) | frozenset(email_hash(e) for e in emails),
        )
