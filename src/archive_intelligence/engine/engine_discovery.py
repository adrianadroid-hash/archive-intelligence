"""Runtime discovery for inventory-, ledger- and manifest-derived expectations.

MVP v0.2 Batch 2 (commercial generalization). This module replaces fixed
current-archive literals (conversation/message/node totals, shard counts,
ZIP member ordinals, ``range(7)``, ``shard + 3``, staged-file counts) with
values derived exclusively from verified runtime artifacts:

  * the Phase 1 inventory (``outputs/ARCHIVE_INVENTORY.json``) for which ZIP
    members are conversation shards and which members are ancillary JSON;
  * the Phase 2 staging ledger (``extraction_manifest.json``) for which
    staged files back which shard and their entry ordinals;
  * the Phase 2 run manifest (``run_manifest.json``) for verified totals
    and per-shard counts.

Standard library only. No configuration system, no machine paths, no
network, no private values in error output. Failures raise
``DiscoveryError``, a ``RuntimeError`` carrying a fixed uppercase code
only, so every existing consumer's safe-error handling applies unchanged.

Determinism: every discovery result is sorted by a structural key (ZIP
ordinal or shard id), so ordering never depends on filesystem or dict
iteration order.
"""
from __future__ import annotations

import re

SHARD_NAME_RE = re.compile(r"^conversations(?:[_-](\d+))?\.json$", re.IGNORECASE)

# Keys reconciled between the Phase 2 run manifest and its per-shard rows.
TOTAL_COUNT_KEYS = (
    "conversations",
    "messages",
    "nodes",
    "active_nodes",
    "alternative_nodes",
)
PER_SHARD_KEYS = ("shard", "conversations", "nodes", "messages")


class DiscoveryError(RuntimeError):
    """A failed discovery carrying a fixed uppercase code only."""


def _fail(code):
    raise DiscoveryError(code)


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def shard_id_from_name(name):
    """Structural shard id parsed from a conversation member filename.

    ``conversations-003.json`` -> 3, ``conversations_3.json`` -> 3,
    ``conversations.json`` -> 0. Returns None when the name is not a
    supported conversation member name.
    """
    if not isinstance(name, str):
        return None
    match = SHARD_NAME_RE.fullmatch(name)
    if match is None:
        return None
    if match.group(1) is None:
        return 0
    return int(match.group(1))


def conversation_members(inventory):
    """Conversation JSON members from the Phase 1 inventory, ZIP-ordinal order.

    Requires the inventory's own ``conversation_files`` records (produced by
    the allowlist-based intake), validates their shape, and requires that
    their parsed shard ids are present and distinct so downstream shard
    numbering stays one-to-one with staged files.
    """
    if not isinstance(inventory, dict):
        _fail("DISCOVERY_INVENTORY")
    rows = inventory.get("conversation_files")
    if not isinstance(rows, list) or not rows:
        _fail("DISCOVERY_NO_CONVERSATIONS")
    ordered = []
    for entry in rows:
        if not isinstance(entry, dict):
            _fail("DISCOVERY_CONVERSATION_ENTRY")
        ordinal = entry.get("zip_directory_index")
        name = entry.get("safe_name")
        if not _is_count(ordinal) or not isinstance(name, str) or not name:
            _fail("DISCOVERY_CONVERSATION_ENTRY")
        if not isinstance(entry.get("entry_id"), str) or not entry.get("entry_id"):
            _fail("DISCOVERY_CONVERSATION_ENTRY")
        if not isinstance(entry.get("path_sha256"), str) or not entry.get("path_sha256"):
            _fail("DISCOVERY_CONVERSATION_ENTRY")
        if not _is_count(entry.get("uncompressed_bytes")):
            _fail("DISCOVERY_CONVERSATION_ENTRY")
        if shard_id_from_name(name) is None:
            _fail("DISCOVERY_CONVERSATION_NAME")
        ordered.append(entry)
    ordered.sort(key=lambda entry: entry["zip_directory_index"])
    identifiers = [shard_id_from_name(entry["safe_name"]) for entry in ordered]
    if len(set(identifiers)) != len(identifiers):
        _fail("DISCOVERY_SHARD_DUPLICATE")
    if [entry["zip_directory_index"] for entry in ordered] != sorted(
        entry["zip_directory_index"] for entry in ordered
    ):
        _fail("DISCOVERY_CONVERSATION_ORDER")
    return ordered


def metadata_members(inventory):
    """Ancillary JSON members (non-conversation, non-directory), ordinal order."""
    if not isinstance(inventory, dict):
        _fail("DISCOVERY_INVENTORY")
    entries = inventory.get("entries")
    if not isinstance(entries, list):
        _fail("DISCOVERY_INVENTORY")
    selected = []
    for entry in entries:
        if not isinstance(entry, dict):
            _fail("DISCOVERY_INVENTORY_ENTRY")
        if entry.get("is_directory") or entry.get("extension") != ".json":
            continue
        if entry.get("is_conversation_json"):
            continue
        ordinal = entry.get("zip_directory_index")
        if not _is_count(ordinal) or not isinstance(entry.get("entry_id"), str):
            _fail("DISCOVERY_INVENTORY_ENTRY")
        selected.append(entry)
    selected.sort(key=lambda entry: entry["zip_directory_index"])
    return selected


def staging_plan(inventory):
    """``[(entry, destination)]``: conversation shards plus all ancillary JSON.

    The metadata staging boundary is structural, never archive-specific:
    every non-conversation JSON member is staged (attachment-resolution
    roles are later discovered from content shape during indexing), and
    non-JSON members (HTML, payloads, media) are never extracted.
    Destinations stay alias-based: ``conversations/<safe_name>`` for the
    allowlisted conversation members, ``metadata/<entry_id>.json`` for
    aliased ancillary members, exactly like the original selective
    staging layout.
    """
    conversations = conversation_members(inventory)
    metadata = metadata_members(inventory)
    plan = [
        (entry, "conversations/" + entry["safe_name"]) for entry in conversations
    ]
    plan.extend(
        (entry, "metadata/" + entry["entry_id"] + ".json") for entry in metadata
    )
    entry_ids = [entry["entry_id"] for entry, _ in plan]
    destinations = [destination for _, destination in plan]
    if len(set(entry_ids)) != len(entry_ids):
        _fail("DISCOVERY_PLAN_ENTRY_DUPLICATE")
    if len(set(destinations)) != len(destinations):
        _fail("DISCOVERY_PLAN_DESTINATION_DUPLICATE")
    for entry, destination in plan:
        if not _is_count(entry.get("zip_directory_index")):
            _fail("DISCOVERY_PLAN_ENTRY")
        if not _is_count(entry.get("uncompressed_bytes")):
            _fail("DISCOVERY_PLAN_ENTRY")
    return plan


def shard_files_from_ledger(ledger):
    """``{shard_id: (basename, destination, entry_id)}`` from the staging ledger.

    Replaces fixed member ordinals (``entry-{shard + 3:06d}``) and fixed
    naming relationships: the ledger is the authority for which verified
    staged file backs which shard. Requires every ledger record to be
    ``verified``; shard ids must be distinct. Sorted by shard id.
    """
    if not isinstance(ledger, dict):
        _fail("DISCOVERY_LEDGER")
    files = ledger.get("files")
    if not isinstance(files, dict) or not files:
        _fail("DISCOVERY_LEDGER")
    found = {}
    for entry_id in sorted(files):
        record = files[entry_id]
        if not isinstance(record, dict) or record.get("status") != "verified":
            _fail("DISCOVERY_LEDGER_ENTRY")
        destination = record.get("destination")
        if not isinstance(destination, str) or not destination:
            _fail("DISCOVERY_LEDGER_ENTRY")
        if not destination.startswith("conversations/"):
            continue
        basename = destination.rsplit("/", 1)[-1]
        shard_id = shard_id_from_name(basename)
        if shard_id is None:
            _fail("DISCOVERY_SHARD_NAME")
        if shard_id in found:
            _fail("DISCOVERY_SHARD_DUPLICATE")
        if not isinstance(entry_id, str) or not entry_id:
            _fail("DISCOVERY_LEDGER_ENTRY")
        found[shard_id] = (basename, destination, entry_id)
    if not found:
        _fail("DISCOVERY_NO_SHARDS")
    return {shard_id: found[shard_id] for shard_id in sorted(found)}


def derive_expectations(phase2_manifest):
    """``(expected_total, expected_shards)`` from the verified Phase 2 manifest.

    * ``expected_total``: dict with TOTAL_COUNT_KEYS plus
      ``conversations_with_branches``, all non-negative ints.
    * ``expected_shards``: tuple of ``(shard, conversations, nodes, messages)``
      rows, sorted by shard id and unique.

    Structural self-consistency is enforced here: per-shard sums must equal
    the aggregate totals and the manifest's ``shards`` status rows must
    match the per-shard rows, so a torn or half-written manifest cannot
    silently redefine expectations.
    """
    if not isinstance(phase2_manifest, dict):
        _fail("DISCOVERY_MANIFEST")
    if phase2_manifest.get("status") != "verified_complete":
        _fail("DISCOVERY_MANIFEST_STATE")
    if phase2_manifest.get("version") != "phase2-1.0":
        _fail("DISCOVERY_MANIFEST_VERSION")
    summary = phase2_manifest.get("summary")
    counts = summary.get("counts") if isinstance(summary, dict) else None
    if not isinstance(counts, dict):
        _fail("DISCOVERY_SUMMARY")
    total = {}
    for key in TOTAL_COUNT_KEYS:
        value = counts.get(key)
        if not _is_count(value):
            _fail("DISCOVERY_COUNTS")
        total[key] = value
    branches = summary.get("conversations_with_branches")
    if not _is_count(branches):
        _fail("DISCOVERY_BRANCH_COUNT")
    total["conversations_with_branches"] = branches
    per_shard = phase2_manifest.get("per_shard")
    if not isinstance(per_shard, list) or not per_shard:
        _fail("DISCOVERY_PER_SHARD")
    shards = []
    for row in per_shard:
        if not isinstance(row, dict):
            _fail("DISCOVERY_PER_SHARD")
        values = tuple(row.get(key) for key in PER_SHARD_KEYS)
        if not all(_is_count(value) for value in values):
            _fail("DISCOVERY_PER_SHARD")
        shards.append(values)
    shard_ids = [row[0] for row in shards]
    if shard_ids != sorted(shard_ids) or len(set(shard_ids)) != len(shard_ids):
        _fail("DISCOVERY_PER_SHARD_ORDER")
    aggregate = {"conversations": 0, "nodes": 0, "messages": 0}
    for _, conversations, nodes, messages in shards:
        aggregate["conversations"] += conversations
        aggregate["nodes"] += nodes
        aggregate["messages"] += messages
    for key, value in aggregate.items():
        if value != total[key]:
            _fail("DISCOVERY_SHARD_TOTAL_MISMATCH")
    status_rows = phase2_manifest.get("shards")
    if not isinstance(status_rows, list):
        _fail("DISCOVERY_SHARDS_ROWS")
    normalized = []
    for row in status_rows:
        try:
            normalized.append((int(row[0]), int(row[1]), row[2]))
        except (TypeError, ValueError, KeyError, IndexError):
            _fail("DISCOVERY_SHARDS_ROWS")
    expected_rows = [(row[0], row[1], "complete") for row in shards]
    if normalized != expected_rows:
        _fail("DISCOVERY_SHARDS_ROWS")
    return total, tuple(shards)
