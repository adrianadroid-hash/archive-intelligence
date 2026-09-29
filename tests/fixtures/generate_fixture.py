"""Deterministic synthetic ChatGPT export fixture generator.

Fabricated content only. No real conversation content, no values copied from
any real archive. Produces a ChatGPT-shaped export ZIP whose structures match
what the current pipeline parser expects (top-level JSON array shards, the
mapping / current_node / parent / children conversation graph schema), plus a
manifest of expected values.

Design rules:
  * deterministic: fixed timestamps, fixed member order, ZIP_STORED with a
    fixed member timestamp and fixed create_system, so the archive bytes are
    reproducible on any machine (see fixture_manifest.json for the golden
    hashes);
  * parser-verified: every generated conversation is run through
    src/archive_intelligence/engine/phase2_parser.py graph/timestamp/enum
    checks before the fixture is returned - a fixture that would trip a
    graph anomaly fails loudly;
  * declared sentinels: privacy canary strings are declared in SENTINELS and
    clearly marked as test fixtures (synthetic_test_fixtures=true).

Usage:
  py -B tests/fixtures/generate_fixture.py --write-manifest
  py -B tests/fixtures/generate_fixture.py --out <directory>
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

_FIXTURES = Path(__file__).resolve().parent
_PROJECT = _FIXTURES.parents[1]
_SRC = _PROJECT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from archive_intelligence.engine.phase2_parser import ROLES, TYPES, enum, graph, timestamp  # noqa: E402

FIXTURE_VERSION = "synthetic-fixture-1.0"
ZIP_FILENAME = "synthetic_export.zip"
GOLDEN_MANIFEST = _FIXTURES / "fixture_manifest.json"
BASE_TIME = 1700000000.0
SHARED_RESPONSE_BODY = "SYNTHETIC_SHARED_RESPONSE_DELTA synthetic duplicate response body, used twice on purpose."

SENTINELS = {
    "S1": {"value": "SYNTHETIC_TITLE_CANARY_ALPHA", "fixture": True, "location": "conversation title (duplicate-like conversation)"},
    "S2": {"value": "SYNTHETIC_BODY_CANARY_BRAVO", "fixture": True, "location": "user message body"},
    "S3": {"value": "sentinel.persona@example.invalid", "fixture": True, "location": "user message body", "note": "RFC 2606 reserved .invalid domain; never a real address"},
    "S4": {"value": "SYNTHETIC_PATH_CANARY_CHARLIE", "fixture": True, "location": "path-like string inside a message body"},
    "S5": {"value": "private-notes-draft", "fixture": True, "location": "ZIP member name that must be aliased by intake"},
    "S6": {"value": "SYNTHETIC_AUX_CANARY_DELTA", "fixture": True, "location": "auxiliary metadata dictionary value"},
    "S7": {"value": "+000-000-00000", "fixture": True, "location": "phone-shaped string inside a message body", "note": "all zeros; never a real number"},
}

PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 56


# --------------------------------------------------------------------------
# conversation builders
# --------------------------------------------------------------------------
def _message(mid, role, parts, create_time, update_time=None, content_type="text", omit=()):
    body = {
        "id": mid,
        "author": {"role": role, "metadata": {}},
        "create_time": create_time,
        "update_time": update_time,
        "status": "finished_successfully",
        "end_turn": None,
        "weight": 1.0,
        "metadata": {},
        "recipient": "all",
        "content": {"content_type": content_type, "parts": parts},
    }
    for key in omit:
        body.pop(key, None)
    return body


def _node(nid, parent, message, create_time):
    return {"id": nid, "message": message, "parent": parent, "create_time": create_time}


def _finish(mapping, include_children=True):
    derived = {key: [] for key in mapping}
    for key, node in mapping.items():
        parent = node.get("parent")
        if isinstance(parent, str) and parent in derived:
            derived[parent].append(key)
    if include_children:
        for key, node in mapping.items():
            node["children"] = derived[key]
    else:
        for node in mapping.values():
            node.pop("children", None)
    return mapping


def _conversation(cid, title, mapping, current_node, create_time, update_time,
                  include_children=True, drop_keys=()):
    conversation = {
        "title": title,
        "create_time": create_time,
        "update_time": update_time,
        "mapping": _finish(mapping, include_children),
        "current_node": current_node,
        "id": cid,
        "moderation_results": [],
        "plugin_id": None,
    }
    for key in drop_keys:
        conversation.pop(key, None)
    return conversation


def build_conversations():
    """Return the 12 fabricated conversations (3 shards: 6 / 4 / 2)."""
    conversations = []

    # C0 - linear; body S2; shares its response body with C8.
    u1 = _message("c0-u1", "user", [SENTINELS["S2"]["value"] + " please summarize the synthetic plan."], BASE_TIME)
    a1 = _message("c0-a1", "assistant", [SHARED_RESPONSE_BODY], BASE_TIME + 5)
    conversations.append(_conversation(
        "synthetic-conv-c0", "Synthetic onboarding conversation",
        {"root": _node("root", None, None, None),
         "c0-u1": _node("c0-u1", "root", u1, BASE_TIME),
         "c0-a1": _node("c0-a1", "c0-u1", a1, BASE_TIME + 5)},
        "c0-a1", BASE_TIME, BASE_TIME + 5))

    # C1 - branching: one fork, continuation on branch B.
    u1 = _message("c1-u1", "user", ["Synthetic follow-up question that forces a branch."], BASE_TIME + 60)
    a_a = _message("c1-aA", "assistant", ["Synthetic alternative reply A."], BASE_TIME + 65)
    a_b = _message("c1-aB", "assistant", ["Synthetic alternative reply B, the branch that continues."], BASE_TIME + 66)
    u2 = _message("c1-u2", "user", ["Synthetic continuation on branch B."], BASE_TIME + 120)
    conversations.append(_conversation(
        "synthetic-conv-c1", "Synthetic branching reply test",
        {"root": _node("root", None, None, None),
         "c1-u1": _node("c1-u1", "root", u1, BASE_TIME + 60),
         "c1-aA": _node("c1-aA", "c1-u1", a_a, BASE_TIME + 65),
         "c1-aB": _node("c1-aB", "c1-u1", a_b, BASE_TIME + 66),
         "c1-u2": _node("c1-u2", "c1-aB", u2, BASE_TIME + 120)},
        "c1-u2", BASE_TIME + 60, BASE_TIME + 120))

    # C2 - deep chain of 12 messages with one three-way fork at message 8.
    mapping = {"root": _node("root", None, None, None)}
    previous = "root"
    messages = []
    for index in range(1, 13):
        if index == 9:
            previous = "c2-s8"
        elif index > 1:
            previous = f"c2-s{index - 1}"
        if index in (10, 11):
            previous = "c2-s8"          # alternative leaves off the fork
        role = "user" if index % 2 else "assistant"
        content_type = "code" if index == 10 else "text"
        parts = ["def synthetic() -> int:\n    return 42"] if index == 10 else [
            f"Synthetic message number {index} in the deep chain conversation used for structural probes."]
        body = _message(f"c2-s{index}", role, parts, BASE_TIME + 200 + index, content_type=content_type)
        messages.append(body)
        mapping[f"c2-s{index}"] = _node(f"c2-s{index}", previous, body, BASE_TIME + 200 + index)
    conversations.append(_conversation(
        "synthetic-conv-c2", "Synthetic deep chain with multi-fork",
        mapping, "c2-s12", BASE_TIME + 201, BASE_TIME + 212))

    # C3 - system and tool roles; non-ASCII title exercises raw UTF-8 in shard 0.
    sysmsg = _message("c3-sys", "system", ["Synthetic system instruction."], BASE_TIME + 300)
    u1 = _message("c3-u1", "user", ["Synthetic request handled with a tool call."], BASE_TIME + 301)
    tool = _message("c3-t1", "tool", ['{"synthetic":"tool-result"}'], BASE_TIME + 302, content_type="text")
    a1 = _message("c3-a1", "assistant", ["Synthetic final answer after the tool call."], BASE_TIME + 303)
    conversations.append(_conversation(
        "synthetic-conv-c3", "Synthetic system prompt conversation — café prêt",
        {"root": _node("root", None, None, None),
         "c3-sys": _node("c3-sys", "root", sysmsg, BASE_TIME + 300),
         "c3-u1": _node("c3-u1", "c3-sys", u1, BASE_TIME + 301),
         "c3-t1": _node("c3-t1", "c3-u1", tool, BASE_TIME + 302),
         "c3-a1": _node("c3-a1", "c3-t1", a1, BASE_TIME + 303)},
        "c3-a1", BASE_TIME + 300, BASE_TIME + 303))

    # C4 - optional/missing fields: no children keys, no update_time keys,
    # minimal message objects, path-like canary S4 in the body.
    u1 = _message("c4-u1", "user", ["Map entry: C:\\synthetic\\%s\\note.txt" % SENTINELS["S4"]["value"]],
                  BASE_TIME + 400, omit=("update_time", "status", "weight", "end_turn", "recipient"))
    a1 = _message("c4-a1", "assistant", ["Synthetic answer without optional fields."],
                  BASE_TIME + 401, omit=("update_time", "status", "weight", "end_turn", "recipient"))
    conversations.append(_conversation(
        "synthetic-conv-c4", "Synthetic optional fields conversation",
        {"root": _node("root", None, None, None),
         "c4-u1": _node("c4-u1", "root", u1, BASE_TIME + 400),
         "c4-a1": _node("c4-a1", "c4-u1", a1, BASE_TIME + 401)},
        "c4-a1", BASE_TIME + 400, None, include_children=False, drop_keys=("update_time",)))

    # C5 - multimodal part object + attachment-shaped reference; email canary S3.
    u1 = _message("c5-u1", "user", ["Synthetic note sent to %s during the test." % SENTINELS["S3"]["value"]],
                  BASE_TIME + 500)
    a1 = _message("c5-a1", "assistant", [
        {"asset_pointer": "file-service://fixture-asset/synthetic-image-01.png",
         "size_bytes": 1024, "width": 64, "height": 64}],
        BASE_TIME + 501, content_type="multimodal_text")
    conversations.append(_conversation(
        "synthetic-conv-c5", "Synthetic multimodal reference",
        {"root": _node("root", None, None, None),
         "c5-u1": _node("c5-u1", "root", u1, BASE_TIME + 500),
         "c5-a1": _node("c5-a1", "c5-u1", a1, BASE_TIME + 501)},
        "c5-a1", BASE_TIME + 500, BASE_TIME + 501))

    # C6 - assistant internal messages (thoughts, reasoning_recap).
    u1 = _message("c6-u1", "user", ["Synthetic reasoning request."], BASE_TIME + 600)
    thoughts = _message("c6-t1", "assistant", ["Synthetic internal reasoning draft."],
                        BASE_TIME + 601, content_type="thoughts")
    recap = _message("c6-r1", "assistant", ["Synthetic reasoning recap."],
                     BASE_TIME + 602, content_type="reasoning_recap")
    a1 = _message("c6-a1", "assistant", ["Synthetic visible answer after reasoning."], BASE_TIME + 603)
    conversations.append(_conversation(
        "synthetic-conv-c6", "Synthetic reasoning trace",
        {"root": _node("root", None, None, None),
         "c6-u1": _node("c6-u1", "root", u1, BASE_TIME + 600),
         "c6-t1": _node("c6-t1", "c6-u1", thoughts, BASE_TIME + 601),
         "c6-r1": _node("c6-r1", "c6-t1", recap, BASE_TIME + 602),
         "c6-a1": _node("c6-a1", "c6-r1", a1, BASE_TIME + 603)},
        "c6-a1", BASE_TIME + 600, BASE_TIME + 603))

    # C7 - edge cases: empty title, empty parts, unknown content type.
    u1 = _message("c7-u1", "user", ["Synthetic placeholder message."], BASE_TIME + 700)
    a1 = _message("c7-a1", "assistant", [], BASE_TIME + 701, content_type="synthetic_widget")
    conversations.append(_conversation(
        "synthetic-conv-c7", "",
        {"root": _node("root", None, None, None),
         "c7-u1": _node("c7-u1", "root", u1, BASE_TIME + 700),
         "c7-a1": _node("c7-a1", "c7-u1", a1, BASE_TIME + 701)},
        "c7-a1", BASE_TIME + 700, BASE_TIME + 701))

    # C8 - duplicate-like: title canary S1, response body identical to C0's
    # response body. The conversation id stays unique: Phase 3 fail-closes on
    # duplicate conversation identities (identity_digest is the cross-phase
    # conversation key), so the duplicate test rides on the shared body group.
    u1 = _message("c8-u1", "user", ["Synthetic duplicate body demonstration."], BASE_TIME + 500)
    a1 = _message("c8-a1", "assistant", [SHARED_RESPONSE_BODY], BASE_TIME + 505)
    conversations.append(_conversation(
        "synthetic-conv-c8", SENTINELS["S1"]["value"],
        {"root": _node("root", None, None, None),
         "c8-u1": _node("c8-u1", "root", u1, BASE_TIME + 500),
         "c8-a1": _node("c8-a1", "c8-u1", a1, BASE_TIME + 505)},
        "c8-a1", BASE_TIME + 500, BASE_TIME + 505))

    # C9 - ordering edge (non-monotonic message times; structural root nodes
    # carry null node timestamps) plus the third fork.
    u1 = _message("c9-u1", "user", ["Synthetic message with out-of-order timestamps."], BASE_TIME + 3600)
    a1 = _message("c9-a1", "assistant", ["Synthetic reply recorded earlier than its question."], BASE_TIME + 60)
    a2 = _message("c9-a2", "assistant", ["Synthetic active reply with a shifted timestamp."], BASE_TIME + 70)
    a2alt = _message("c9-a2alt", "assistant", ["Synthetic alternative reply after a long gap."], BASE_TIME + 7200)
    conversations.append(_conversation(
        "synthetic-conv-c9", "Synthetic ordering edge with fork",
        {"root": _node("root", None, None, None),
         "c9-u1": _node("c9-u1", "root", u1, BASE_TIME + 3600),
         "c9-a1": _node("c9-a1", "c9-u1", a1, BASE_TIME + 60),
         "c9-a2": _node("c9-a2", "c9-a1", a2, None),
         "c9-a2alt": _node("c9-a2alt", "c9-a1", a2alt, BASE_TIME + 7200)},
        "c9-a2", BASE_TIME + 60, BASE_TIME + 7200))

    # C10 - Unicode stress; second message uses an unknown role value.
    u1 = _message("c10-u1", "user", [
        "Synthetic unicode: élève café combining mark, emoji \U0001F469‍\U0001F469‍\U0001F467‍\U0001F466 ZWJ, "
        "RTL שלום مرحبا, CJK 日本語テスト, astral \U0001D5D8."],
        BASE_TIME + 800)
    a1 = _message("c10-a1", "synthetic_moderator", ["Synthetic reply from an unknown role value."],
                  BASE_TIME + 801)
    conversations.append(_conversation(
        "synthetic-conv-c10", "Synthetic unicode —Ελληνικά עברית العربية 日本語\U0001F9E0",
        {"root": _node("root", None, None, None),
         "c10-u1": _node("c10-u1", "root", u1, BASE_TIME + 800),
         "c10-a1": _node("c10-a1", "c10-u1", a1, BASE_TIME + 801)},
        "c10-a1", BASE_TIME + 800, BASE_TIME + 801))

    # C11 - timestamp boundaries; phone-shaped canary S7.
    u1 = _message("c11-u1", "user", ["Synthetic message at a far-past in-range timestamp."], 157766400.0)
    a1 = _message("c11-a1", "assistant", ["Synthetic callback number %s is not a real number." % SENTINELS["S7"]["value"]],
                  2051222400.5)
    conversations.append(_conversation(
        "synthetic-conv-c11", "Synthetic timestamp boundary conversation",
        {"root": _node("root", None, None, None),
         "c11-u1": _node("c11-u1", "root", u1, 157766400.0),
         "c11-a1": _node("c11-a1", "c11-u1", a1, 2051222400.5)},
        "c11-a1", 946684800.0, 2051222400.5))

    return conversations


# --------------------------------------------------------------------------
# self-check and expectations
# --------------------------------------------------------------------------
def _self_check(conversations):
    """Fail loudly if a generated conversation would trip a parser gate."""
    unknown_roles, unknown_types = set(), set()
    for conversation in conversations:
        check = graph(conversation)
        if check["anomalies"]:
            raise RuntimeError("FIXTURE_GRAPH_ANOMALY")
        if not check["active_valid"]:
            raise RuntimeError("FIXTURE_ACTIVE_PATH_INVALID")
        timestamp(conversation.get("create_time"))
        timestamp(conversation.get("update_time"))
        for node in conversation["mapping"].values():
            message = node.get("message")
            if message is None:
                continue
            timestamp(message.get("create_time"))
            timestamp(message.get("update_time"))
            role = enum(message.get("author", {}).get("role"), ROLES)
            content_type = enum(message.get("content", {}).get("content_type"), TYPES)
            if role.startswith("unknown:"):
                unknown_roles.add(role)
            if content_type.startswith("unknown:"):
                unknown_types.add(content_type)
    if len(unknown_roles) != 1 or len(unknown_types) != 1:
        raise RuntimeError("FIXTURE_ENUM_SHAPE")
    return sorted(unknown_roles)[0], sorted(unknown_types)[0]


def _expectations(conversations, unknown_role, unknown_type):
    role_hist = collections.Counter()
    type_hist = collections.Counter()
    body_counts = collections.Counter()
    id_counts = collections.Counter()
    totals = collections.Counter()
    branch_points = extra_branches = branched = 0
    for conversation in conversations:
        check = graph(conversation)
        nodes = len(conversation["mapping"])
        messages = sum(1 for n in conversation["mapping"].values() if n.get("message") is not None)
        totals["conversations"] += 1
        totals["messages"] += messages
        totals["nodes"] += nodes
        totals["active_nodes"] += len(check["active"])
        totals["alternative_nodes"] += nodes - len(check["active"])
        branch_points += check["branch_points"]
        extra_branches += check["extra_branches"]
        if check["branch_points"]:
            branched += 1
        id_counts[conversation.get("id")] += 1
        for node in conversation["mapping"].values():
            message = node.get("message")
            if message is None:
                continue
            role = enum(message.get("author", {}).get("role"), ROLES)
            content_type = enum(message.get("content", {}).get("content_type"), TYPES)
            role_hist[role if role in ROLES else "unknown"] += 1
            type_hist[content_type if content_type in TYPES else "unknown"] += 1
            for part in message.get("content", {}).get("parts", []):
                if isinstance(part, str):
                    body_counts[part] += 1
    body_groups = sorted((count for count in body_counts.values() if count > 1), reverse=True)
    id_groups = sorted((count for count in id_counts.values() if count > 1), reverse=True)
    if body_groups != [2] or id_groups != []:
        raise RuntimeError("FIXTURE_DUPLICATE_SHAPE")
    shared_body_sha = hashlib.sha256(SHARED_RESPONSE_BODY.encode("utf-8")).hexdigest()
    totals["branched_conversations"] = branched
    totals["branch_points"] = branch_points
    totals["extra_branches"] = extra_branches
    return {
        "totals": dict(sorted(totals.items())),
        "roles": dict(sorted(role_hist.items())),
        "content_types": dict(sorted(type_hist.items())),
        "unknown_enum_values": {"role": unknown_role, "content_type": unknown_type},
        "duplicates": {
            "body_group_sizes": body_groups,
            "shared_conversation_id_group_sizes": id_groups,
            "shared_body_sha256": shared_body_sha,
        },
    }


# --------------------------------------------------------------------------
# members and archive
# --------------------------------------------------------------------------
def _shard_json(conversations, ensure_ascii):
    return (json.dumps(conversations, ensure_ascii=ensure_ascii, indent=1) + "\n").encode("utf-8")


def build_members():
    """Ordered (name, bytes) pairs for the synthetic export ZIP (21 members)."""
    conversations = build_conversations()
    unknown_role, unknown_type = _self_check(conversations)
    shard0 = _shard_json(conversations[0:6], ensure_ascii=False)
    shard1 = _shard_json(conversations[6:10], ensure_ascii=False)
    shard2 = _shard_json(conversations[10:12], ensure_ascii=True)

    members = [
        ("chat.html", b"<!doctype html>\n<html><body>synthetic fixture placeholder</body></html>\n"),
        ("user.json", b'{"id": "synthetic-user-0001", "email": null, "picture": null, "name": null}\n'),
        ("conversations-000.json", shard0),
        ("conversations-001.json", shard1),
        ("conversations-002.json", shard2),
        ("settings.json", b'{"theme": "synthetic", "share": null}\n'),
        ("model_comparisons.json", b"[]\n"),
        ("message_feedback.json", b"[]\n"),
        ("shared_conversations.json", b"[]\n"),
        ("group_chats.json", b"[]\n"),
        ("shopping.json", b"[]\n"),
        ("sora.json", b"[]\n"),
        ("files.json", b"[]\n"),
        ("attachments.json", b"[]\n"),
        ("manifest.json", b'{"type": "synthetic-fixture"}\n'),
        ("export_metadata.json", b'{"export_version": "synthetic-1.0"}\n'),
        ("private-notes-draft.png", PNG_HEADER),
        ("aux_names.json", json.dumps({
            "synthetic-key-alpha": SENTINELS["S6"]["value"],
            "synthetic-key-beta": "plain-fixture-value",
        }, ensure_ascii=True, indent=1).encode("utf-8") + b"\n"),
        ("file_metadata.json", json.dumps(
            [{"file_id": "synthetic-file-01", "file_name": "synthetic-report.pdf",
              "mime_type": "application/pdf"}],
            ensure_ascii=True, indent=1).encode("utf-8") + b"\n"),
        ("payload.dat", PNG_HEADER),
        ("payload.bundle", b"\x00\x01SYNTHETIC_OPAQUE_PAYLOAD_FIXTURE" + b"\x00" * 40),
    ]
    return members, conversations, unknown_role, unknown_type


def build_zip(members) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 0
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def build():
    """Return (manifest dict, zip bytes)."""
    members, conversations, unknown_role, unknown_type = build_members()
    zip_bytes = build_zip(members)
    expectations = _expectations(conversations, unknown_role, unknown_type)
    shard_names = ["conversations-000.json", "conversations-001.json", "conversations-002.json"]
    manifest = {
        "fixture_version": FIXTURE_VERSION,
        "generator": "tests/fixtures/generate_fixture.py",
        "content_policy": "All conversation content is fabricated. Synthetic test fixtures only.",
        "synthetic_test_fixtures": True,
        "zip": {
            "filename": ZIP_FILENAME,
            "bytes": len(zip_bytes),
            "sha256": hashlib.sha256(zip_bytes).hexdigest(),
            "member_count": len(members),
            "compression": "stored",
        },
        "members": [
            {
                "ordinal": ordinal,
                "name": name,
                "bytes": len(data),
                "crc32": f"{zipfile.crc32(data) & 0xFFFFFFFF:08x}",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for ordinal, (name, data) in enumerate(members)
        ],
        "shards": [
            {
                "ordinal": ordinal,
                "name": name,
                "conversations": count,
                "encoding": encoding,
            }
            for ordinal, (name, count, encoding) in enumerate([
                ("conversations-000.json", 6, "utf-8-raw"),
                ("conversations-001.json", 4, "utf-8-raw"),
                ("conversations-002.json", 2, "ascii-escaped"),
            ])
        ],
        "expected_alias": {"private-notes-draft.png": "entry-000016.png"},
        "graph_expectations": {"all_conversations_anomaly_free": True},
        "sentinels": SENTINELS,
        **expectations,
    }
    return manifest, zip_bytes


def write_golden(path=GOLDEN_MANIFEST):
    manifest, _ = build()
    path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="generate_fixture.py")
    parser.add_argument("--out", type=Path, default=None, help="write the fixture ZIP into this directory")
    parser.add_argument("--write-manifest", action="store_true", help="refresh the committed golden manifest")
    args = parser.parse_args(argv)
    manifest, zip_bytes = build()
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / ZIP_FILENAME).write_bytes(zip_bytes)
        print(f"FIXTURE_ZIP_WRITTEN members={manifest['zip']['member_count']} bytes={len(zip_bytes)}")
    if args.write_manifest:
        write_golden()
        print(f"FIXTURE_MANIFEST_WRITTEN path={GOLDEN_MANIFEST.name}")
    if not args.out and not args.write_manifest:
        print(f"FIXTURE_OK members={manifest['zip']['member_count']} "
              f"conversations={manifest['totals']['conversations']} messages={manifest['totals']['messages']} "
              f"sha256={manifest['zip']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
