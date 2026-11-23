"""Compare a recipient's real Google group cards with Share mappings, and fix drift.

The roster banner in CFS only sees mapping lists. This module reads the actual
cards in each recipient's shared group, so it also finds name-duplicate cards,
cards linked to the wrong person, and foreign cards sitting in the group.

Everything runs synchronously for one recipient per call. Cloud Run only gives
the process CPU while a request is open, so background work would stall.

Check mode never writes. Fix mode refuses when the plan looks suspicious (too
many changes, short reads, queued push jobs) instead of guessing.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Any, Callable

from .. import db
from ..google_auth import owner_credentials, recipient_credentials
from ..people_api import get_group_member_ids, get_people_batch, is_rate_limit_error
from ..people_recipient import (
    delete_contact,
    ensure_contact_in_group,
    normalize_person_resource_name,
    remove_contact_from_group,
)
from .recipient_import import contacts_identity_match, contacts_name_match

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHANGES = 15
HARD_MAX_CHANGES = 40
# Relinks and add-backs only edit mappings or labels, so they get a larger cap.
SAFE_MAX_CHANGES = 120
NAME_FIELDS = "names,emailAddresses,phoneNumbers"
OWNER_CACHE_SECONDS = 300
MIN_OWNER_READ_RATIO = 0.9
MAX_LABELS = 40
# Google allows about 90 contact reads a minute per user, and CFS waits 120s for a
# reply. Fix mode paces itself, stops at the budget, and reports partial=True so the
# caller can simply run it again.
FIX_BUDGET_SECONDS = 100.0
PUSH_PACE_SECONDS = 2.0

_owner_cache: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}

_CHANGE_KEYS = (
    "not_in_group",
    "sibling",
    "relink",
    "create",
    "repair",
    "duplicates",
    "foreign",
)


def _norm(value: Any) -> str:
    return normalize_person_resource_name(str(value or "")) or ""


def person_label(person: dict[str, Any] | None) -> str:
    names = (person or {}).get("names") or []
    first = names[0] if names and isinstance(names[0], dict) else {}
    family = str(first.get("familyName") or "").strip()
    given = str(first.get("givenName") or "").strip()
    if family and given:
        return f"{family}, {given}"
    return family or given or str(first.get("displayName") or "").strip() or "(no name)"


def classify_group(
    *,
    owner_people: dict[str, dict[str, Any]],
    mappings: dict[str, dict[str, Any]],
    members: list[str],
    tracked_all: set[str],
    recipient_people: dict[str, dict[str, Any]],
    sibling_cards: dict[str, str] | None = None,
    pending_owner_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Pure planning step. No network, no writes.

    owner_people:     owner_person_id -> owner person (names/emails/phones)
    mappings:         owner_person_id -> sync mapping row for this group
    members:          resource names currently in the recipient's group
    tracked_all:      every recipient_person_id Share maps for this owner/recipient
    recipient_people: recipient resource name -> person (names/emails/phones)
    sibling_cards:    owner_person_id -> a card that exists on the recipient and is
                      already mapped from another group. Reused before anything is
                      adopted or created so one contact never gets two cards.
    pending_owner_ids: owner ids another group's plan will give a card to (check
                      mode only, where that plan has not been applied yet).
    """
    sibling_cards = sibling_cards or {}
    pending_owner_ids = pending_owner_ids or set()
    member_set = {_norm(item) for item in members if _norm(item)}
    tracked = {_norm(item) for item in tracked_all if _norm(item)}
    rid_counts = Counter(
        _norm(m.get("recipient_person_id"))
        for m in mappings.values()
        if _norm(m.get("recipient_person_id"))
    )

    plan: dict[str, Any] = {
        "healthy": 0,
        "not_in_group": [],  # (owner_id, rid) card exists and is correct, but not in the group
        "sibling": [],  # (owner_id, rid) reuse the card another group already maps
        "relink": [],  # (owner_id, extra_rid) adopt an existing untracked card
        "create": [],  # owner_id needs a brand new card
        "repair": [],  # (owner_id, rid) card is the wrong person, no better card exists
        "duplicates": [],  # rid, extra card that name-matches a healthy contact
        "foreign": [],  # rid, card that matches nobody on the owner list
        "conflicts": [],  # (owner_id, rid) two owner ids share one card, left alone
        "owner_missing": [],  # owner_id, owner person could not be read
        "freed": [],  # rids released when a wrong link is replaced
    }

    need_card: list[str] = []
    wrong: list[tuple[str, str]] = []
    healthy: set[str] = set()

    for owner_id, mapping in sorted(mappings.items()):
        owner_person = owner_people.get(owner_id)
        if owner_person is None:
            plan["owner_missing"].append(owner_id)
            continue
        rid = _norm(mapping.get("recipient_person_id"))
        card = recipient_people.get(rid) if rid else None
        if not rid or card is None:
            need_card.append(owner_id)
            continue
        if rid_counts[rid] > 1:
            plan["conflicts"].append((owner_id, rid))
            healthy.add(owner_id)
        elif not contacts_name_match(owner_person, card):
            wrong.append((owner_id, rid))
        else:
            healthy.add(owner_id)
        if rid not in member_set:
            plan["not_in_group"].append((owner_id, rid))

    extras = sorted(
        rid for rid in member_set if rid not in tracked and rid in recipient_people
    )
    claimed: set[str] = set()

    def pick(owner_person: dict[str, Any]) -> str:
        free = [e for e in extras if e not in claimed]
        for matcher in (contacts_identity_match, contacts_name_match):
            for rid in free:
                if matcher(owner_person, recipient_people[rid]):
                    claimed.add(rid)
                    return rid
        return ""

    this_group_rids = set(rid_counts)
    tracked_elsewhere = tracked - this_group_rids
    adopted_or_new: set[str] = set()

    # Wrong links first. A card released by a wrong link (Bob's card that was
    # mapped to Brenda) becomes available for its real owner or for cleanup.
    for owner_id, old_rid in wrong:
        found = pick(owner_people[owner_id])
        if found:
            plan["relink"].append((owner_id, found))
            if old_rid in member_set and old_rid not in tracked_elsewhere:
                plan["freed"].append(old_rid)
        else:
            plan["repair"].append((owner_id, old_rid))
        adopted_or_new.add(owner_id)

    extras = extras + sorted(rid for rid in plan["freed"] if rid not in extras)

    for owner_id in need_card:
        sibling = _norm(sibling_cards.get(owner_id))
        if sibling and sibling in recipient_people:
            plan["sibling"].append((owner_id, sibling))
            adopted_or_new.add(owner_id)
            continue
        if owner_id in pending_owner_ids:
            plan["sibling"].append((owner_id, ""))
            adopted_or_new.add(owner_id)
            continue
        found = pick(owner_people[owner_id])
        if found:
            plan["relink"].append((owner_id, found))
        else:
            plan["create"].append(owner_id)
        adopted_or_new.add(owner_id)

    # A replaced wrong card no longer needs to be added back to the group.
    relinked_owner_ids = {oid for oid, _ in plan["relink"]}
    plan["not_in_group"] = [
        (oid, rid) for oid, rid in plan["not_in_group"] if oid not in relinked_owner_ids
    ]

    healthy_after = healthy | adopted_or_new
    for rid in [e for e in extras if e not in claimed]:
        card = recipient_people.get(rid)
        if card is None:
            continue
        if any(contacts_name_match(owner_people[o], card) for o in healthy_after):
            plan["duplicates"].append(rid)
        else:
            plan["foreign"].append(rid)

    plan["healthy"] = len(healthy)
    plan["changes"] = sum(len(plan[key]) for key in _CHANGE_KEYS)
    return plan


def _labels(items: list[str]) -> list[str]:
    return items[:MAX_LABELS]


def describe_plan(
    plan: dict[str, Any],
    owner_people: dict[str, dict[str, Any]],
    recipient_people: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Readable summary for CFS. Names only, no resource ids beyond counts."""

    def owner_names(ids: list[str]) -> list[str]:
        return _labels(sorted(person_label(owner_people.get(i)) for i in ids))

    def card_names(ids: list[str]) -> list[str]:
        return _labels(sorted(person_label(recipient_people.get(i)) for i in ids))

    return {
        "healthy": plan["healthy"],
        "changes": plan["changes"],
        "missing": owner_names(list(plan["create"])),
        "adopt": owner_names([oid for oid, _ in plan["relink"]]),
        "linked_elsewhere": owner_names([oid for oid, _ in plan["sibling"]]),
        "wrong_card": owner_names([oid for oid, _ in plan["repair"]]),
        "not_in_group": owner_names([oid for oid, _ in plan["not_in_group"]]),
        "duplicates": card_names(list(plan["duplicates"])),
        "foreign": card_names(list(plan["foreign"])),
        "conflicts": owner_names([oid for oid, _ in plan["conflicts"]]),
        "owner_missing_count": len(plan["owner_missing"]),
        "counts": {
            "missing": len(plan["create"]),
            "adopt": len(plan["relink"]),
            "linked_elsewhere": len(plan["sibling"]),
            "wrong_card": len(plan["repair"]),
            "not_in_group": len(plan["not_in_group"]),
            "duplicates": len(plan["duplicates"]),
            "foreign": len(plan["foreign"]),
        },
    }


def _risky_changes(plan: dict[str, Any]) -> int:
    """Changes that delete, create, or rewrite a card. Relinks only edit mappings."""
    return (
        len(plan["create"])
        + len(plan["repair"])
        + len(plan["duplicates"])
        + len(plan["foreign"])
    )


def _load_owner_people(owner: str, creds: Any, owner_ids: list[str]) -> dict[str, dict[str, Any]]:
    now = time.time()
    cached_at, cached = _owner_cache.get(owner, (now, {}))
    if now - cached_at > OWNER_CACHE_SECONDS:
        cached_at, cached = now, {}
    wanted = sorted({_norm(item) for item in owner_ids if _norm(item)})
    missing = [item for item in wanted if item not in cached]
    if missing:
        fetched = get_people_batch(creds, missing, person_fields=NAME_FIELDS)
        cached = {**cached, **fetched}
    _owner_cache[owner] = (cached_at, cached)
    return {item: cached[item] for item in wanted if item in cached}


class _Cards:
    """Recipient cards read once per run. Missing (404) ids are remembered too."""

    def __init__(self, creds: Any) -> None:
        self.creds = creds
        self.people: dict[str, dict[str, Any]] = {}
        self.gone: set[str] = set()

    def load(self, rids: Any) -> None:
        wanted = sorted(
            {_norm(item) for item in rids if _norm(item)} - set(self.people) - self.gone
        )
        if not wanted:
            return
        got = get_people_batch(self.creds, wanted, person_fields=NAME_FIELDS)
        self.people.update(got)
        self.gone.update(item for item in wanted if item not in got)

    def forget(self, rid: str) -> None:
        self.people.pop(rid, None)
        self.gone.add(rid)


def _recipient_groups(owner: str, recipient: str) -> list[dict[str, str]]:
    from .app_data import _recipient_group_resource

    data = db.get_app_data(recipient) or {}
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for group in data.get("sharedGroups") or []:
        if not isinstance(group, dict):
            continue
        if db.normalize_email(str(group.get("owner") or "")) != owner:
            continue
        share_id = str(group.get("shareId") or "").strip()
        resource = str(group.get("resourceName") or "").strip()
        group_id = _recipient_group_resource(group)
        if not (share_id and resource and group_id):
            continue
        key = (share_id, resource)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "share_id": share_id,
                "resource_name": resource,
                "group_id": group_id,
                "name": str(group.get("name") or "Shared Group"),
            }
        )
    return out


def _group_mappings(owner: str, recipient: str, group: dict[str, str]) -> dict[str, dict[str, Any]]:
    by_owner, _ = db.load_sync_mappings(
        group["share_id"], owner, group["resource_name"], recipient
    )
    return {_norm(k): v for k, v in by_owner.items() if _norm(k)}


def _recipient_rows(owner: str, recipient: str) -> list[dict[str, Any]]:
    """Every mapping row with a card id for this owner and recipient, any group."""
    return [
        row
        for row in db.list_sync_mappings_for_recipient(recipient)
        if db.normalize_email(str(row.get("owner") or "")) == owner
    ]


def _refusal(group: dict[str, str], reason: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "group": group["name"],
        "share_id": group["share_id"],
        "ok": False,
        "reason": reason,
        "message": message,
        **extra,
    }


def _check_group(
    *,
    owner: str,
    recipient: str,
    group: dict[str, str],
    creds: Any,
    owner_creds: Any,
    cards: _Cards,
    max_changes: int,
    apply: bool,
    pending: dict[str, set[str]],
    deadline: float,
) -> dict[str, Any]:
    mappings = _group_mappings(owner, recipient, group)
    if not mappings:
        return {"group": group["name"], "share_id": group["share_id"], "ok": True,
                "empty": True, "members": 0, "mapped": 0, "changes": 0}

    rows = _recipient_rows(owner, recipient)
    try:
        members = [_norm(m) for m in get_group_member_ids(creds, group["group_id"], max_members=2000)]
        # Check mode only: skip cards an earlier group's plan would already delete.
        members = [m for m in members if m and m not in pending["deleted"]]
        cards.load({_norm(r.get("recipient_person_id")) for r in rows} | set(members))
        owner_people = _load_owner_people(owner, owner_creds, list(mappings))
    except Exception as exc:  # noqa: BLE001 - report, never crash the whole recipient
        logger.warning("Roster group read failed for %s %s: %s", recipient, group["name"], exc)
        return _refusal(group, "read_failed", "Could not read this group from Google.", error=str(exc)[:200])

    if len(owner_people) / max(1, len(mappings)) < MIN_OWNER_READ_RATIO:
        return _refusal(
            group, "owner_read_short",
            "Only part of the owner's contact list could be read, so nothing was changed.",
            members=len(members), mapped=len(mappings),
        )
    if not members and any(_norm(m.get("recipient_person_id")) for m in mappings.values()):
        return _refusal(
            group, "group_read_empty",
            "Google returned an empty group, so nothing was changed.",
            members=0, mapped=len(mappings),
        )

    # A card another group already maps (and that still exists) is reused.
    sibling_rows: dict[tuple[str, str], dict[str, Any]] = {}
    sibling_cards: dict[str, str] = {}
    for row in rows:
        owner_id = _norm(row.get("owner_person_id"))
        rid = _norm(row.get("recipient_person_id"))
        same_group = (
            row.get("share_id") == group["share_id"]
            and row.get("group_resource_name") == group["resource_name"]
        )
        if same_group or owner_id not in mappings or rid not in cards.people:
            continue
        sibling_cards.setdefault(owner_id, rid)
        sibling_rows[(owner_id, rid)] = row

    plan = classify_group(
        owner_people=owner_people,
        mappings=mappings,
        members=members,
        tracked_all={_norm(r.get("recipient_person_id")) for r in rows}
        | (pending["rids"] if not apply else set()),
        recipient_people=cards.people,
        sibling_cards=sibling_cards,
        pending_owner_ids=pending["owners"] if not apply else set(),
    )
    if not apply:
        # Later groups see what this plan would do, so check mode matches a real fix.
        pending["owners"].update(oid for oid, _ in plan["relink"])
        pending["owners"].update(plan["create"])
        pending["rids"].update(rid for _, rid in plan["relink"] if rid)
        pending["deleted"].update(plan["duplicates"])

    report: dict[str, Any] = {
        "group": group["name"],
        "share_id": group["share_id"],
        "ok": True,
        "members": len(members),
        "mapped": len(mappings),
        **describe_plan(plan, owner_people, cards.people),
    }
    if not apply or plan["changes"] == 0:
        return report

    if time.monotonic() > deadline:
        report["partial"] = True
        return report
    risky = _risky_changes(plan)
    if risky > max_changes or plan["changes"] > SAFE_MAX_CHANGES:
        report.update(
            ok=False,
            reason="too_many_changes",
            message=(
                f"{plan['changes']} changes ({risky} that delete or create cards) are more "
                f"than the safe limit. Nothing was changed."
            ),
        )
        return report

    applied = _apply_plan(
        owner=owner,
        recipient=recipient,
        group=group,
        creds=creds,
        owner_creds=owner_creds,
        plan=plan,
        mappings=mappings,
        sibling_rows=sibling_rows,
        cards=cards,
        deadline=deadline,
    )
    report["applied"] = applied
    report["partial"] = bool(applied["partial"])
    report["ok"] = not applied["errors"]
    return report


def _prune_orphan_rows(
    *, owner: str, recipient: str, groups: list[dict[str, str]], cards: _Cards, apply: bool
) -> int:
    """Mapping rows for a group the recipient no longer has, pointing at a card
    Google no longer has. They help nothing and only add noise to counts."""
    live = {(g["share_id"], g["resource_name"]) for g in groups}
    rows = [
        r
        for r in _recipient_rows(owner, recipient)
        if (r.get("share_id"), r.get("group_resource_name")) not in live
    ]
    if not rows:
        return 0
    cards.load({_norm(r.get("recipient_person_id")) for r in rows})
    dead = [r for r in rows if _norm(r.get("recipient_person_id")) in cards.gone]
    if apply:
        for row in dead:
            db.delete_sync_mapping_rows(
                str(row.get("share_id") or ""),
                owner,
                str(row.get("group_resource_name") or ""),
                recipient,
                [str(row.get("owner_person_id") or "")],
            )
    return len(dead)


def _retry(action: Callable[[], bool], attempts: int = 3) -> bool:
    for attempt in range(attempts):
        if action():
            time.sleep(0.7)  # stay under Google's per-minute write quota
            return True
        if attempt < attempts - 1:
            time.sleep(2.0 * (attempt + 1))
    return False


def _fresh_mapping(
    *, share_id: str, owner: str, resource: str, recipient: str, owner_id: str
) -> dict[str, Any] | None:
    by_owner, _ = db.load_sync_mappings(share_id, owner, resource, recipient)
    for key, row in by_owner.items():
        if _norm(key) == owner_id:
            return row
    return None


def _apply_plan(
    *,
    owner: str,
    recipient: str,
    group: dict[str, str],
    creds: Any,
    owner_creds: Any,
    plan: dict[str, Any],
    mappings: dict[str, dict[str, Any]],
    sibling_rows: dict[tuple[str, str], dict[str, Any]],
    cards: _Cards,
    deadline: float,
) -> dict[str, Any]:
    from .contact_sync import _apply_mapping_update

    applied: dict[str, Any] = {
        "relinked": 0,
        "linked_elsewhere": 0,
        "created": 0,
        "repaired": 0,
        "added_back": 0,
        "deleted": 0,
        "ungrouped": 0,
        "partial": False,
        "errors": [],
    }
    errors: list[str] = applied["errors"]

    def stop() -> bool:
        if applied["partial"]:
            return True
        if time.monotonic() > deadline:
            applied["partial"] = True
        return applied["partial"]

    need_full = sorted(
        {oid for oid, _ in plan["relink"]}
        | set(plan["create"])
        | {oid for oid, _ in plan["repair"]}
    )
    full_people: dict[str, dict[str, Any]] = {}
    if need_full:
        try:
            full_people = get_people_batch(owner_creds, need_full)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"owner read failed: {str(exc)[:120]}")
            return applied
    owner_token = str(getattr(owner_creds, "token", "") or "") or None

    def push(owner_id: str, rid: str, bucket: str) -> None:
        try:
            _push(owner_id, rid, bucket)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{bucket} failed for {owner_id}: {str(exc)[:100]}")
            if is_rate_limit_error(exc):
                applied["partial"] = True
        time.sleep(PUSH_PACE_SECONDS)

    def _push(owner_id: str, rid: str, bucket: str) -> None:
        member = full_people.get(owner_id)
        if not member:
            errors.append(f"owner contact unreadable: {owner_id}")
            return
        mapping = mappings.get(owner_id) or {}
        db.upsert_sync_mapping(
            share_id=group["share_id"],
            owner=owner,
            group_resource_name=group["resource_name"],
            recipient_email=recipient,
            owner_person_id=owner_id,
            recipient_person_id=rid,
            last_hash="",
            last_photo_hash="",
            owner_etag="",
            last_assignment_label=str(mapping.get("last_assignment_label") or ""),
        )
        fresh = _fresh_mapping(
            share_id=group["share_id"],
            owner=owner,
            resource=group["resource_name"],
            recipient=recipient,
            owner_id=owner_id,
        )
        if not fresh:
            errors.append(f"mapping missing after update: {owner_id}")
            return
        outcome = _apply_mapping_update(
            owner=owner,
            mapping=fresh,
            member_data=member,
            owner_etag=str(member.get("etag") or ""),
            owner_token=owner_token,
        )
        if outcome.get("status") in {"updated", "created"} or outcome.get("reason") == "photo_failed":
            applied[bucket] += 1
        elif outcome.get("reason") == "etag_unavailable":
            applied["partial"] = True
            errors.append(f"{bucket} paused for {owner_id}: Google rate limit")
        else:
            errors.append(f"{bucket} failed for {owner_id}: {outcome.get('reason') or outcome.get('status')}")

    # Reuse and adopt existing cards first, delete next, create last and one at a
    # time, so a retry never makes a second copy.
    for owner_id, rid in plan["sibling"]:
        if stop():
            break
        if not rid:
            continue
        source = sibling_rows.get((owner_id, rid)) or {}
        mapping = mappings.get(owner_id) or {}
        db.upsert_sync_mapping(
            share_id=group["share_id"],
            owner=owner,
            group_resource_name=group["resource_name"],
            recipient_email=recipient,
            owner_person_id=owner_id,
            recipient_person_id=rid,
            last_hash=str(source.get("last_hash") or ""),
            last_photo_hash=str(source.get("last_photo_hash") or ""),
            owner_etag=str(source.get("owner_etag") or ""),
            last_assignment_label=str(
                source.get("last_assignment_label") or mapping.get("last_assignment_label") or ""
            ),
        )
        if _retry(lambda rid=rid: ensure_contact_in_group(creds, rid, group["group_id"])):
            applied["linked_elsewhere"] += 1
        else:
            errors.append(f"could not add shared card to group: {owner_id}")
    for owner_id, rid in plan["relink"]:
        if stop():
            break
        push(owner_id, rid, "relinked")
    for owner_id, rid in plan["repair"]:
        if stop():
            break
        push(owner_id, rid, "repaired")
    for owner_id, rid in plan["not_in_group"]:
        if stop():
            break
        if _retry(lambda rid=rid: ensure_contact_in_group(creds, rid, group["group_id"])):
            applied["added_back"] += 1
        else:
            errors.append(f"could not add card back to group: {owner_id}")
    for rid in plan["duplicates"]:
        if stop():
            break
        if _retry(lambda rid=rid: delete_contact(creds, rid)):
            applied["deleted"] += 1
            cards.forget(rid)
            # Any group that still mapped this card would otherwise point at nothing.
            db.delete_sync_mappings_for_recipient_card(recipient, rid)
        else:
            errors.append(f"could not delete duplicate card: {rid}")
    for rid in plan["foreign"]:
        if stop():
            break
        if _retry(lambda rid=rid: remove_contact_from_group(creds, rid, group["group_id"])):
            applied["ungrouped"] += 1
        else:
            errors.append(f"could not remove foreign card from group: {rid}")
    for owner_id in plan["create"]:
        if stop():
            break
        push(owner_id, "", "created")
    return applied


def run_recipient_roster_check(
    owner_email: str,
    recipient_email: str,
    *,
    apply: bool = False,
    max_changes: int = DEFAULT_MAX_CHANGES,
) -> dict[str, Any]:
    owner = db.normalize_email(owner_email)
    recipient = db.normalize_email(recipient_email)
    base = {"recipient": recipient, "mode": "fix" if apply else "check"}

    creds = recipient_credentials(recipient)
    if not creds:
        return {**base, "ok": False, "reason": "recipient_not_connected",
                "message": "This recipient has not connected Google to Share."}
    owner_creds = owner_credentials(owner)
    if not owner_creds:
        return {**base, "ok": False, "reason": "owner_not_connected",
                "message": "The owner's Google sign-in is not stored in Share."}
    if apply:
        busy = db.count_active_push_jobs_for_owner(owner)
        if busy:
            return {**base, "ok": False, "reason": "push_queue_busy",
                    "message": f"{busy} Share update job(s) are still running. Try again in a few minutes.",
                    "busy_jobs": busy}

    # Biggest group first: the all-contacts group usually holds the real cards,
    # so smaller meeting groups can reuse them instead of creating copies.
    groups = sorted(
        _recipient_groups(owner, recipient),
        key=lambda g: -len(_group_mappings(owner, recipient, g)),
    )
    cards = _Cards(creds)
    deadline = time.monotonic() + FIX_BUDGET_SECONDS
    pending: dict[str, set[str]] = {"owners": set(), "rids": set(), "deleted": set()}
    results = [
        _check_group(
            owner=owner,
            recipient=recipient,
            group=group,
            creds=creds,
            owner_creds=owner_creds,
            cards=cards,
            max_changes=max_changes,
            apply=apply,
            pending=pending,
            deadline=deadline,
        )
        for group in groups
    ]
    try:
        orphan_rows = _prune_orphan_rows(
            owner=owner, recipient=recipient, groups=groups, cards=cards, apply=apply
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Orphan row cleanup failed for %s: %s", recipient, exc)
        orphan_rows = 0
    if apply:
        _owner_cache.pop(owner, None)
    changes = sum(int(r.get("changes") or 0) for r in results)
    partial = any(r.get("partial") for r in results)
    return {
        **base,
        "ok": all(r.get("ok", True) for r in results),
        "groups": results,
        "changes": changes,
        "partial": partial,
        "orphan_rows": orphan_rows,
        "clean": changes == 0 and not partial and all(r.get("ok", True) for r in results),
    }
