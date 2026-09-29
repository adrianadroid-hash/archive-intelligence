"""Render privacy-minimized Phase 1 reports from the generated inventory only."""
from pathlib import Path
import collections, datetime, hashlib, json, re

from .engine_discovery import staging_plan

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs'
d=json.loads((OUT/'ARCHIVE_INVENTORY.json').read_text(encoding='utf-8'))
entries=d['entries']
def mb(n): return f'{n/1_000_000:,.2f} MB'
def gb(n): return f'{n/1_000_000_000:,.3f} GB'
def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join('---' for _ in headers)+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])
def save(name, text):
    path=ROOT/name
    temp=path.with_suffix(path.suffix+'.tmp')
    # P1-5/M23: forced LF, matching phase2_common; Path.write_text would
    # otherwise translate '\n' to the platform newline and emit CRLF reports.
    temp.write_text(text.strip()+'\n',encoding='utf-8',newline='\n')
    temp.replace(path)

conv=d['conversation_files']
conv_bytes=sum(r['uncompressed_bytes'] for r in conv)
conv_compressed=sum(r['compressed_bytes'] for r in conv)
# Batch 2: the staging set is the same runtime discovery rule Phase 2 uses
# (conversation JSON plus every ancillary JSON member). No fixed entry IDs.
plan=staging_plan(d)
stage_metadata=[entry for entry,dest in plan if dest.startswith('metadata/')]
stage_files=conv+stage_metadata
stage_bytes=sum(e['uncompressed_bytes'] for e in stage_files)
stage_peak=stage_bytes+max(e['uncompressed_bytes'] for e in stage_files)
json_total=d['by_extension'].get('.json',{}).get('count',0)
ancillary_json_count=max(0,json_total-len(conv))
dat_count=d['by_extension'].get('.dat',{}).get('count',0)
bundle_count=d['by_extension'].get('.bundle',{}).get('count',0)
payloads=[e for e in entries if e['extension'] in {'.dat','.bundle'}]
payload_count=len(payloads)
formats=d['opaque_payload_header_scan']['formats']
media_types={'JPEG','PNG','WebP','WAV','ID3 audio candidate','MPEG audio candidate','ISO-BMFF media container'}
media_count=sum(formats[k]['count'] for k in media_types if k in formats)
media_bytes=sum(formats[k]['uncompressed_bytes'] for k in media_types if k in formats)
document_types={'PDF','ZIP-family container','OLE compound document','SQLite','gzip container'}
document_count=sum(formats[k]['count'] for k in document_types if k in formats)
unidentified_count=formats.get('unidentified',{}).get('count',0)
dates_min=min(s['sampled_timestamp_min'] for s in d['schema_samples'] if s['sampled_timestamp_min'])
dates_max=max(s['sampled_timestamp_max'] for s in d['schema_samples'] if s['sampled_timestamp_max'])
dup=d['possible_duplicate_member_groups']
byid={e['entry_id']:e for e in entries}
dup_member_count=sum(len(g) for g in dup)
dup_extra_bytes=sum(byid[g[0]]['uncompressed_bytes']*(len(g)-1) for g in dup)
schema_bytes=sum(s['bytes_read'] for s in d['schema_samples'])
ancillary_bytes=sum(s['bytes_read'] for s in d['ancillary_schema_samples'])

# P2-6/M8: the configured source location is never rendered. Reports carry an
# alias plus a fingerprint of the location so the owner can confirm it locally.
source_fp=hashlib.sha256(d['source_path'].encode('utf-8')).hexdigest()

# Conversation naming shape (runtime-derived, not assumed).
conversation_names=[r['safe_name'] for r in conv]
numbered_conversation_shards=all(re.fullmatch(r'conversations[-_]\d+\.json',n,re.I) for n in conversation_names)
single_conversation_file=conversation_names==['conversations.json']
if numbered_conversation_shards:
    source_type_text=f'one ZIP containing {len(conv)} numbered conversation JSON shards'
elif single_conversation_file:
    source_type_text='one ZIP containing a single conversations.json'
else:
    source_type_text=f'one ZIP containing {len(conv)} conversation JSON files'
conversation_shards_distinct=len({(e['uncompressed_bytes'],e['crc32']) for e in conv})==len(conv)
duplicate_member_name_groups=sum(1 for c in collections.Counter(e['path_sha256'] for e in entries).values() if c>1)
schema_sample_names=', '.join(s['safe_name'] for s in d['schema_samples'])

# ZIP packaging timestamp range (runtime-derived from member directory stamps).
zip_stamps=[tuple(e['zip_timestamp']) for e in entries if not e['is_directory']]
def zip_stamp(ts): return '%04d-%02d-%02d %02d:%02d:%02d'%tuple(ts[:6])
zip_ts_min=zip_stamp(min(zip_stamps)) if zip_stamps else 'n/a'
zip_ts_max=zip_stamp(max(zip_stamps)) if zip_stamps else 'n/a'

# Sampled graph evidence (runtime-derived from schema samples).
graph_checks=[s['bounded_graph_check'] for s in d['schema_samples']]
complete_graphs=[g for g in graph_checks if isinstance(g,dict) and 'node_count' in g]
node_counts_list=', '.join(str(g['node_count']) for g in complete_graphs)
null_counts_list=', '.join(str(g['null_message_nodes']) for g in complete_graphs)
sampled_multi_child=sum(g.get('parents_with_multiple_children',0) for g in complete_graphs)
sampled_children_field=sum(g.get('nodes_with_children_field',0) for g in complete_graphs)
sampled_current_resolves=all(g.get('current_node_resolves') for g in complete_graphs) if complete_graphs else False
sampled_parents_resolve=all(g.get('parent_links_resolve') for g in complete_graphs) if complete_graphs else False
observed_roles=sorted({r for s in d['schema_samples'] for r in s['observed_roles']})
observed_ctypes=sorted({c for s in d['schema_samples'] for c in s['observed_content_types']})

# P2-6/M10: environment-neutral sync caveat. No provider-brand claims and no
# path-component brand detection; sync status is reported as unverified and
# the owner resolves storage/sync facts locally before any staging.
sync_line='**Cloud-sync/backup status of the source and project paths was not verified during Phase 1.**'
sync_caveat='Cloud-sync or backup agents may apply to the source and primary project paths. Sync status, filesystem encryption, access permissions and retention were not checked or changed; other sync or backup agents may still apply. The inspection script makes no network requests. Phase 1 reports contain sanitized metadata only.'
sync_open_question='Cloud-sync/backup behavior and acceptable storage for any future content-bearing derivatives.'
sync_preflight='storage/sync handling'

# Attachment-metadata evidence discovered from ancillary schema samples.
file_record_sample=next((s for s in d['ancillary_schema_samples'] if '$[].file_id' in s['paths'] and '$[].file_name' in s['paths']),None)
file_record_label=f"{file_record_sample['entry_id']}.json" if file_record_sample else 'the ancillary array with file_id/file_name fields'
alias_sample=next((s for s in d['ancillary_schema_samples'] if s['root_type']=='object' and all(t==['string'] for p,t in s['paths'].items() if p!='$')),None)
alias_label=f"{alias_sample['entry_id']}.json" if alias_sample else 'the string-valued ancillary object'

chat_entry=next((e for e in entries if e['safe_name']=='chat.html'),None)
chat_note=(f"`chat.html` is {mb(chat_entry['uncompressed_bytes'])}; it was not opened or rendered. Do not count it as additional conversations without comparison." if chat_entry else '`chat.html` is not present in this export.')

manifest=f'''# Archive manifest — Phase 1

Inspected: {d['inspected_at_utc']}. All sizes are decimal MB/GB unless stated otherwise.

## Source and scope

- Exact source: `<source-archive>` (configured source ZIP; path withheld from this report). Location SHA-256 `{source_fp}` is computed over the configured path, so the archive owner can confirm which location was used without this report revealing it. The full path remains only in ARCHIVE_INVENTORY.json.
- Source type: **{source_type_text}**; not an extracted folder.
- The attached source directory contained exactly one item, this ZIP. No alternate source was selected.
- ZIP size: **{d['source_archive_bytes']:,} bytes ({gb(d['source_archive_bytes'])})**.
- Members: **{d['file_count']:,} files**, no directory entries; logical uncompressed total **{d['uncompressed_total_bytes']:,} bytes ({gb(d['uncompressed_total_bytes'])})**.
- Compressed member total: {d['compressed_members_total_bytes']:,} bytes; the remaining ZIP bytes are container overhead.
- Free disk space on the inspection volume before inspection/extraction: **{d['disk_free_bytes_before']:,} bytes ({gb(d['disk_free_bytes_before'])})**. Nothing was extracted to disk.
- Source size and modification time were unchanged across inspection. This is not a byte-for-byte integrity proof.

## Files by extension

{table(['Extension','Count','Uncompressed','Compressed'],[(ext,v['count'],mb(v['uncompressed_bytes']),mb(v['compressed_bytes'])) for ext,v in d['by_extension'].items()])}

## Conversation files

{table(['ZIP entry ID','Filename','Exact uncompressed bytes','Compressed bytes'],[(r['entry_id'],r['safe_name'],f"{r['uncompressed_bytes']:,}",f"{r['compressed_bytes']:,}") for r in conv])}

Total conversation JSON: **{conv_bytes:,} bytes ({mb(conv_bytes)})**, {mb(conv_compressed)} compressed. Largest conversation file: {mb(max(r['uncompressed_bytes'] for r in conv))}.
The other {ancillary_json_count} JSON files are ancillary records; sampled structures may include file metadata, feedback, sharing and account metadata. Their values were not included in reports. {chat_note}

## Largest files

{table(['Safe location','Uncompressed size','Header identification'],[(r['safe_name'],mb(r['uncompressed_bytes']),r.get('header_format','not header-scanned')) for r in d['largest_files']])}

Private/unknown member names are replaced with `entry-NNNNNN.ext`. Resolve a location locally using the zero-based `zip_directory_index` in ARCHIVE_INVENTORY.json against the unchanged ZIP central directory. `path_sha256` verifies the member name without storing it. These aliases are not original filenames. Known generic export filenames above are preserved.

## Media and attachments

There are **{len(payloads):,} opaque payload files**: {dat_count:,} `.dat` and {bundle_count:,} `.bundle`, totaling **{gb(sum(e['uncompressed_bytes'] for e in payloads))}** uncompressed. Extensions alone would misleadingly report zero media.
Only the first 64 bytes per opaque payload were read, {d['opaque_payload_header_scan']['total_bytes_read']:,} bytes total. No image viewing, OCR, audio transcription, media decoding or document-body parsing occurred.

{table(['Header family','Count','Logical size'],[(k,v['count'],mb(v['uncompressed_bytes'])) for k,v in formats.items()])}

Headers identify **{media_count:,} media or media candidates ({gb(media_bytes)})**, {document_count:,} document/container candidates, and {unidentified_count:,} unidentified payloads. ISO-BMFF may include video, audio or images; ID3/MPEG are candidates, not decoder-validated classifications. ZIP-family could be Office documents or archives. Payload counts do not imply unique attachments or one-to-one message associations.

## Dates

- ZIP member timestamps range from **{zip_ts_min} to {zip_ts_max}**, timezone unspecified. These indicate packaging timestamps, not conversation history.
- Bounded sample timestamps span **{dates_min} to {dates_max}**. This is a sampled span only, not the earliest/latest archive dates.
- Exact conversation count, message count and full history date range are deliberately **not calculated** in Phase 1.
- Shard numbering is not a reliable chronological ordering: sampled shards contain mixed dates.

## Duplicate assessment

One source export ZIP was found; there is no second export container in the attached source folder. {'There are no duplicate ZIP member names.' if duplicate_member_name_groups==0 else f'There are {duplicate_member_name_groups} duplicate member-name groups; review before staging.'} {'There are no equal-size/equal-CRC conversation file candidates.' if conversation_shards_distinct else 'Some conversation file candidates share size+CRC; review before staging.'}
There are **{len(dup)} nonempty size+CRC duplicate candidate groups**, covering **{dup_member_count} member entries**. Their potential repeated bytes beyond one copy per group total {mb(dup_extra_bytes)}; this is not verified reclaimable space. CRC collisions and repeated assets are possible. No files were deleted or deduplicated.
Duplicate conversations across shards, historical re-exports embedded as payloads, and equivalence between HTML and JSON remain unverified. Conversation file naming is not evidence of duplicate exports.

## Schema evidence and streaming

The final inspection sampled {len(d['schema_samples'])} 64 KiB prefixes: {schema_sample_names}; **{schema_bytes:,} bytes**, {100*schema_bytes/conv_bytes:.3f}% of all conversation JSON. Ancillary JSON sampling read at most 2 KiB per file ({ancillary_bytes:,} bytes total). Small ancillary files may fit completely within that bound. Only structural types, approved role/content-type enums and numeric timestamps were retained.

{table(['Element','Observed representation / limitation'],[
('Conversation root','Sampled conversation files are JSON arrays of conversation objects; not JSONL.'),
('Conversation fields','id / conversation_id strings, title string (value discarded), numeric create_time / update_time, current_node string, mapping object.'),
('mapping','Object keyed by opaque node IDs; node objects contain id, parent and message. Raw IDs are not reported.'),
('Messages','message may be null (root/structural node) or an object; author.role, content and metadata appear.'),
('Branches',('At least one sampled conversation has a parent with multiple child nodes.' if sampled_multi_child else 'No multiple-child parents appeared in the bounded samples; the archive may still branch outside the sample.')+' parent is null or a node ID. Derive child adjacency from parent links.'),
('children',(f'Observed in {sampled_children_field} sampled nodes.' if sampled_children_field else 'Absent from the sampled prefixes.')+' Do not require an explicit children array; validate if encountered later.'),
('current_node',('Resolved to a mapping key in all complete bounded conversation samples.' if sampled_current_resolves else 'Not resolved in every complete bounded sample.')+' Treat the parent chain as a candidate active path; validate graph consistency later.'),
('Roles',f"{', '.join(observed_roles) or 'none'} observed at message.author.role. Preserve other roles if encountered; no archive-wide role census performed."),
('Timestamps','Conversation/message numeric timestamps are consistent with Unix seconds, including fractional seconds. Ancillary feedback timestamps were strings.'),
('Content',f"Observed content types in the samples: {', '.join(observed_ctypes) or 'none'}. Strings were not retained; absence of multimodal samples is not absence of multimodal content."),
('Attachments',(f'Ancillary {file_record_label} is an array with file_id/file_name/mime_type-style fields.' if file_record_sample else 'No ancillary file_id/file_name array appeared in the samples.')+' Payload members exist separately. Message-to-file linkage and path resolution remain unverified; attachment pointers in bounded conversation samples remain unverified.'),
('Streaming','Yes: read each ZipExtFile sequentially with an incremental JSON array/event parser; no need to load the entire archive or extract media.')])}

{len(complete_graphs)} complete first-conversation graph samples contained {node_counts_list} nodes ({null_counts_list} null-message nodes respectively). Parent references resolved in every complete sample: {'yes' if sampled_parents_resolve else 'no'}. These are schema probes, not representative statistics.

The bundled Python runtime is available; Phase 1 probes use the standard library only. The Phase 1 tokenizer is a bounded structural probe, **not a production JSON validator or a Phase 2 parser**. The unsampled conversation files and complete JSON validity have not been verified.
'''

privacy=f'''# Privacy risk report — Phase 1

## What was and was not inspected

Inspection was local and read-only: ZIP directory metadata, bounded JSON prefixes, and 64-byte payload headers. No source content was sent through web, connector, model-analysis or upload tools. Only sanitized structural summaries were returned to the task. No complete messages, titles, contact details, credential values, raw node IDs, image previews or document contents were printed or saved in reports.
No full extraction, full conversation parse, semantic analysis, classification, embeddings, OCR or transcription was performed. Source size and mtime remained unchanged; a full hash was not computed.

## Potential secret-bearing types and locations

These are **risk locations**, not claims that actual secrets were found.

{table(['Location','Potential exposure','Handling'],[
(', '.join(conversation_names[:1])+(' through '+conversation_names[-1] if len(conversation_names)>1 else ''),'Messages, titles and metadata can contain passwords, tokens, contact details and intimate material.','Read via bounded local streams; never emit values or log parser excerpts.'),
((chat_entry['entry_id']+' chat.html' if chat_entry else 'chat.html (absent)'),'Rendered conversation content; HTML can include external URLs or active resources.','Not opened; avoid browser rendering during structural work.'),
('user.json / account metadata entries','Account and identity metadata.','Only schema types sampled; exclude identity values from indexes/reports.'),
(file_record_label,'Attachment file_name / file_id / mime_type metadata and structured identifiers.','Names and IDs withheld; store a controlled local linkage index only in a later authorized phase.'),
('message_feedback.json / shared_conversations.json','Feedback text, titles, conversation references and sharing identifiers.','Values withheld; do not follow sharing links.'),
('Other ancillary JSON entries, including '+alias_label,'Auxiliary metadata may expose paths, URLs, filenames or account context.','Unknown keys collapsed to [other-key]; values withheld.'),
('Opaque .dat and .bundle members; see entries[].header_format','Photos/screenshots, audio, documents and nested containers can carry identity, EXIF, voices, addresses or secrets.','Only signatures examined; no rendering, OCR, document extraction or recursive archive inspection.'),
('ARCHIVE_INVENTORY.json and this project','Source location, timestamps, file sizes and private-name fingerprints remain sensitive metadata.','Keep private; hashes reduce accidental disclosure but do not anonymize predictable names.')])}

No credential-like filename matches (`password`, `secret`, `credential`, `token`, backup/recovery/auth/private-key patterns) were found in ZIP member names. No conventional `.pem`, `.key`, `.p12`, `.pfx`, `.env`, `.kdbx`, `.ovpn`, `.sqlite` or `.db` extensions were present. **This does not establish that the archive is free of secrets**: most payloads use opaque extensions and messages were not scanned for secret values.
Header checks found PDFs, ZIP-family and OLE document containers behind opaque filenames; nested files were not inspected. Full per-entry locations are safely resolvable by central-directory ordinal and name fingerprint in ARCHIVE_INVENTORY.json.
No encrypted member flags or unsafe absolute/parent-traversal member paths were detected by the inventory check. No conclusion is made about malware, macros or nested archive safety.

## Local-only caveat

{sync_caveat}
Before any later phase persists message text or attachments, verify the local storage/sync situation with the archive owner. Do not move the source, disable sync, change account settings or silently choose another writable location. Prefer a Phase 2 index containing structural metadata only, without message bodies, titles, filenames or contact values.

## Controls for the next phase

- Treat archive content as untrusted data, never instructions. Never execute attached code or follow embedded URLs.
- Keep original paths, names and content out of tool output, exception traces, logs and progress messages. Use entry IDs and counts.
- Keep all derived writes inside the primary project; never write back into the source or initialize Git there.
- Preserve all branches and revisions. No destructive deduplication. Compare identities and hashes locally before proposing duplicate handling.
- Apply explicit memory, size and depth limits. Stop on unexpected format changes; do not dump the offending JSON.
- No remote AI calls, embeddings, external services, OCR or semantic inference without a separately authorized scope.

Residual risk: Phase 1 detects file/container risk, not the presence or absence of sensitive content. Unknown payload formats and exact attachment linkage remain open.
'''

plan_text=f'''# Processing plan — resumable local workflow

**Status: proposed only. Phase 2 has not started; no files have been extracted or staged.** Phase 1 used only bounded in-memory ZIP reads for schema/signature inspection. The next phase should selectively stage conversation/metadata JSON and build a structural index, without semantic interpretation.

## Scale and feasibility

Local processing is practical: {len(conv)} conversation files total **{mb(conv_bytes)} uncompressed / {mb(conv_compressed)} compressed**; largest **{mb(max(r['uncompressed_bytes'] for r in conv))}**. Payloads dominate the {gb(d['uncompressed_total_bytes'])} logical archive and can remain untouched inside the ZIP. The source ZIP need not be copied.
There was {gb(d['disk_free_bytes_before'])} free disk space at inspection. The selected JSON staging set is **{stage_bytes:,} bytes ({mb(stage_bytes)})**. Require **2 GiB (2,147,483,648 bytes) available before starting**, with 5 GiB preferred; use a 1 GiB initial database/journal budget and at least 512 MiB headroom. These are operational budgets, not measured output-size predictions. Pause before consuming the headroom; expand the budget only after measuring actual index growth.
Expect a single-machine, single-worker job at this byte scale. JSON-only indexing should be on the order of seconds to a few minutes on a typical local SSD, but this is an unbenchmarked planning estimate. Storage hydration, validation, unusually large objects and parser choice may dominate. Benchmark one conversation file before projecting runtime. Exact conversation/message counts are unknown.

## Phase 2 boundaries

After explicit authorization and the storage/privacy preflight: selectively extract the {len(plan)} listed JSON files ({len(conv)} conversation, {len(stage_metadata)} ancillary), then stream all {len(conv)} conversation files to obtain counts, exact timestamp bounds, graph integrity and attachment-reference metadata. This requires full structural traversal but **no semantic analysis**. The staged JSON inherently contains private source text; derived indexes/reports must not retain message bodies or titles. Media extraction is deferred to a later reference-driven, specifically authorized step. No OCR, transcription, embeddings, topics, products or story evaluation.

## Selective decompression strategy — Phase 2 only

### 1. Exact extraction allowlist

Extract every conversation JSON member and every ancillary JSON member ({len(plan)} files total; the rule is discovery from ARCHIVE_INVENTORY.json, not a fixed member list). Resolve each member from its zero-based `zip_directory_index` in ARCHIVE_INVENTORY.json, verify its name fingerprint, size and CRC, and write to the controlled destination below. An alias is deliberately used for private/unknown filenames; never guess the original member path.

{table(['Source entry ID','Safe source name','Staging relative destination','Expected bytes'],[(e['entry_id'],e['safe_name'],'conversations/'+e['safe_name'],f"{e['uncompressed_bytes']:,}") for e in conv]+[(e['entry_id'],e['safe_name'],'metadata/'+e['entry_id']+'.json',f"{e['uncompressed_bytes']:,}") for e in stage_metadata])}

Metadata selection rationale: Phase 2 discovers attachment-resolution roles from staged metadata **content shape** — string-valued alias maps, `{{file_id,file_name}}` record arrays and nested `files` manifest objects — never from fixed ordinals or filenames. Observed in the samples: {file_record_label} carries file_id/file_name-style fields and {alias_label} is a string-valued object. Their exact semantic purposes and linkage are **not yet proven**. Treat them as candidate structural mapping sources, inspect locally during Phase 2, and do not publish their values. If linkage needs another metadata file, record the unresolved reference and justify a specific allowlist extension before extracting it.
Keep every non-JSON member — `chat.html` and all payload/media members — compressed; they are not needed for graph indexing and are never staged.

### 2. Expected size and disk requirement

- {len(plan)} selected files: **{stage_bytes:,} bytes ({mb(stage_bytes)})** logical extracted size; small filesystem-allocation overhead is additional.
- Conversation portion: {conv_bytes:,} bytes; metadata portion: {stage_bytes-conv_bytes:,} bytes.
- Conservative staging peak allowing one additional largest `.partial` file: **{stage_peak:,} bytes ({mb(stage_peak)})**, before index/journal/log space. A fresh sequential atomic extraction normally needs less because a partial becomes its final file.
- Check at least **2 GiB free** immediately before staging and after each file; 5 GiB is preferred. This covers the selected files, the initial index/journal budget and 512 MiB reserve. It does not authorize extracting all payloads.
- Before later attachment extraction, compute the sum of unique requested member sizes, plus one largest temporary member, and add the same reserve and remaining index budget. Reject a batch that exceeds its available-space budget.

### 3. Staging layout

All paths below are under the primary project, never inside the source folder. `<archive-sha256>` is the locally computed archive digest, keeping snapshots separate without copying the ZIP.

```text
staging/
  <archive-sha256>/
    conversations/
{chr(10).join('      '+e['safe_name'] for e in conv)}
    metadata/
{chr(10).join('      '+e['entry_id']+'.json' for e in stage_metadata)}
    attachments/                   # initially empty; later approved references only
      <content-sha256>.<verified-ext-or-bin>
    extraction_manifest.json
outputs/
  phase2/
    structural_index.sqlite
    run_manifest.json
    STRUCTURAL_SUMMARY.md
    VALIDATION.json
```

Write temporary files beside their intended destination as `<name>.partial`; never use archive filenames as filesystem destinations. Resolve the final path and ensure it remains under the selected staging root. Use controlled filenames to avoid path traversal and collisions. Stage one member at a time with bounded buffers and an exclusive run lock.

### 4. Payloads remaining compressed initially

**All {payload_count:,} `.dat`/`.bundle` payloads remain in the ZIP**, including recognized images, audio/media containers, PDFs, ZIP-family and OLE documents, and unidentified payloads. Do not extract image thumbnails as a convenience. Do not recursively decompress containers or open documents to determine their subjects.

### 5. Later extraction by conversation reference

During structural indexing, collect attachment references from all branches, not only the active path. Compare message file/asset references with the selected file/path metadata and the original ZIP directory in local memory. Maintain a many-to-many table of conversation digest, node digest, attachment reference digest and archive entry ordinal. Do not assume every content reference is a local attachment; external citation URLs must not be fetched.
For a later authorized conversation selection, resolve its reference set to **unique ZIP members**. Extract only those members. If a reference is ambiguous or missing, record an unresolved-reference code and do not guess. Compute each payload's SHA-256 while streaming, assign a content-addressed safe output name and record a pointer from every referencing conversation. Do not put private original filenames in public logs or reports. Unknown extensions become `.bin`; signatures are not permission to execute a file. Nested archives remain intact unless separately authorized.

### 6. Verification against the original ZIP

Before staging, compute the ZIP's SHA-256 by a read-only sequential pass and record size, mtime and a canonical central-directory fingerprint. For each allowlisted entry, compare its ordinal/name fingerprint/CRC/size with the Phase 1 inventory. Stream it through `ZipFile.open(..., 'r')` into a destination `.partial`, counting bytes and computing CRC32 and SHA-256. Read to EOF so the ZIP reader's CRC check also completes. Require actual byte count and CRC to match ZipInfo. Independently re-read the local `.partial` and require its SHA-256 to equal the streamed source-member SHA-256. Only then atomically rename to the final path and mark it verified.
At completion, recheck the archive size/mtime and recompute the ZIP SHA-256 to confirm that the source bytes still match the preflight digest. A mismatch stops processing and invalidates the run's trust status; never repair or rewrite the original archive. Phase 1 has not calculated that digest or validated full member CRCs.

### 7. Duplicate prevention

Use `(archive_sha256, entry_ordinal, path_sha256, uncompressed_size, CRC32)` as extraction identity. The extraction manifest records destination, verified content hash, byte count, status and tool version. If identity matches and an existing destination's hash matches its recorded hash, skip extraction. Do not overwrite an unexpected existing file; flag it for review.
For payloads referenced by several conversations, use one content-addressed file plus many reference rows. Size+CRC groups are candidate accelerators only, never sufficient evidence to skip distinct content. When a new candidate has the same verified SHA-256 as an existing payload, reuse that payload and discard only the tool-created temporary file. Preserve source membership/provenance for both entries. No source deduplication or deletion.

### 8. Interruption and resume

Record each member as `pending`, `extracting`, `verified` or `failed`; persist the ledger atomically after each transition. After interruption, revalidate source identity before resuming. Rehash verified destination files and skip only verified matches. For a tool-owned `.partial`, restart that member from its beginning rather than resuming at an arbitrary compressed offset. If a final file exists after a rename but before the manifest commit, verify it against a fresh read-only source-member hash/CRC/size, then adopt it into the ledger instead of extracting another copy. Do not remove unrelated or unrecognized files.
Conversation indexing has its own transactional checkpoints, separate from extraction. Staging completion is not indexing completion. Resume an interrupted staged conversation file by replaying from its start to the last committed conversation ordinal, or use a tested parser's verified element-boundary offset. Never resume in the middle of a string or UTF-8 sequence.

### 9. Preserve the archive and privacy boundary

Open the original ZIP only with `rb` / ZIP mode `r`. Never use `extractall`, ZIP append/write modes, rename/move, source-side temporary files, source-side Git, or recursive extraction. All scripts, locks, staged files, hashes, indexes and reports remain in the primary project. No uploads or external services.
{sync_line} Verify sync/storage behavior with the archive owner before staging raw JSON: extraction produces content-bearing copies and may trigger pre-existing sync. No content-bearing staging may begin while that conflicts with the archive owner's no-transmission rule. Do not silently change sync settings or switch writable workspaces. If safe staging cannot be established, remain blocked on staging; a separately agreed direct-from-ZIP metadata-only pass is the fallback, without claiming that selective extraction occurred.

### 10. Phase 1 stop

This section is a strategy, not an extraction command or execution result. No staging directory or decompressed JSON/media has been created during Phase 1.

## Workflow

1. **Preflight, verify and selectively stage.** Read STATUS.md and the privacy report; confirm source identity, read-only policy, primary write root, available space and {sync_preflight}. Follow the complete selective decompression strategy above. Stop on source changes or unresolved no-transmission concerns. Stage only the {len(plan)} allowlisted JSON files after Phase 2 authorization.
2. **Choose and validate a streaming parser.** Use a vetted incremental UTF-8 JSON parser over the verified staged files (or `ZipFile.open` only for an agreed no-staging fallback). Prefer the bundled strict standard-library incremental parser (`scripts/phase2_parser.py`); provision another approved local dependency only if it is tested to the same contract. Do not use `json.load` on whole conversation files and do not reuse the Phase 1 probing tokenizer as the production parser. Test synthetic arrays, escaped strings, split UTF-8, null messages, branches, missing parents, cycles and malformed input first. Use event parsing or spill only permitted structural fields if a single conversation exceeds the memory cap.
3. **Create local resumable state.** Under the primary project's `outputs/phase2/`, create a SQLite structural index and run manifest. Store parser/schema version, source fingerprint, shard ordinal+CRC+size, status (`pending/running/complete/failed`), committed conversation ordinal, count totals, timestamps and safe error codes. Do not store raw message strings, titles, private filenames or diagnostic snippets.
4. **Process one conversation file sequentially.** Parse array elements without assuming chronological order. Index conversation identity as a stable locally derived digest, conversation create/update timestamps, node counts, role counts and content-type counts. Index node IDs as digests namespaced by conversation identity, parent relations, null-message flags and current_node reference. Record unknown roles/types safely without printing arbitrary values; unexpected type strings can be stored as digests with an unknown counter.
5. **Validate graphs and active path.** Derive child adjacency from parent links. Validate IDs, missing references, multiple roots and cycles. Follow current_node to its root for a candidate active path only after validation. Keep all alternative branches; do not treat mapping order or timestamps as traversal order. Distinguish null structural nodes from messages. Compare any explicit children arrays if they appear later.
6. **Map attachments without opening bodies.** Inspect file metadata structure and message reference fields as encountered. Resolve file IDs and member paths locally through the ZIP directory; persist digests and entry ordinals only. Validate linkage instead of guessing from `.dat` names. Record unresolved references. Do not recursively open ZIP-family/OLE/PDF payloads or decode the bundle. The observed file metadata record is {file_record_label}; linkage to individual messages is still unproven.
7. **Commit atomically.** Commit rows, counts and checkpoint together after each conversation or bounded transaction. Write summary snapshots via temporary file + atomic replacement. Mark a conversation file complete only at a valid closing array and EOF, after confirming staging verification. For a direct ZIP fallback, require end-of-member/CRC validation. Failures keep the prior committed state; log only entry ID, parser offset and safe error code.
8. **Resume conservatively.** Revalidate source, staged-file hashes and parser version. Skip completed conversation files. Reopen an interrupted file from its beginning and stream-discard previously committed elements, using stable unique keys to prevent double counting. Do not seek to an arbitrary compressed byte offset or append after an uncommitted partial record. Replay is cheap at the observed file sizes. Any schema or source change requires a new run or explicit migration.
9. **Assess duplicates without deletion.** Compare exact conversation IDs/version timestamps and structural/content digests computed locally if authorized. The same ID can have changed revisions. CRC+size groups from Phase 1 are candidates only. Byte-verify media duplicates only in a separately scoped pass; preserve originals and provenance. Do not double-count chat.html as another export.
10. **Reconcile and stop.** Verify all {len(conv)} conversation files, per-file totals, role/message/node totals, timestamp units, missing attachments and graph anomalies. Produce a metadata-only structural summary plus updated STATUS.md. Stop before semantic analysis, embeddings, classification or content evaluation.

## Suggested output structure

`outputs/phase2/structural_index.sqlite`, `outputs/phase2/run_manifest.json`, `outputs/phase2/STRUCTURAL_SUMMARY.md`, `outputs/phase2/VALIDATION.json`; all within the primary project. These are planned files and do not exist yet.
The source archive is referenced in place. The {len(plan)} selectively staged JSON files are raw source subsets, not a complete archive copy. No initial media tree or extra transcript conversion is required. Keep staged files private; cleanup or retention is a later explicit decision, never a source-side action.

## Open questions resolved only by later authorized work

- Exact number of conversations/messages and full creation/update date ranges.
- Whether any conversation files contain different schemas, unobserved roles/content types, missing nodes or malformed JSON.
- Exact media subtypes and attachment-to-message linkage; meaning/contents of unidentified and bundle payloads.
- Conversation overlap/revisions across files; content-verified duplicate assets.
- {sync_open_question}

## Exact next action

Obtain the archive owner's authorization for **Phase 2 selective JSON staging and local structural indexing**, including resolution of any cloud-sync no-transmission concern before raw staging. Then perform the preflight above. Phase 1 authorization does not permit automatic continuation.
'''

status=f'''# Status — Archive Intelligence

**PHASE 1 COMPLETE — STOPPED AT THE REQUESTED BOUNDARY.**

- Primary writable workspace: the project root containing this file (relative reference; absolute path withheld).
- Source: `<source-archive>` (location SHA-256 `{source_fp[:16]}...`; configured path withheld — full value in ARCHIVE_INVENTORY.json).
- Source type: ZIP containing {len(conv)} conversation JSON files ({source_type_text.split('containing ')[-1]}) and ancillary/payload files.
- Snapshot: {d['inspected_at_utc']}.
- Source bytes: {d['source_archive_bytes']}; source mtime_ns: {d['source_mtime_ns']}.
- Source remained unchanged by size/mtime checks. No full integrity hash was taken.

## Completed

1. Resolved the sole source ZIP and recorded disk space.
2. Inventoried all {d['member_count']:,} central-directory entries, sizes, types, largest files and duplicate candidates.
3. Inspected {len(d['schema_samples'])} bounded conversation prefixes, ancillary JSON prefixes and 64-byte opaque-payload signatures.
4. Documented parent-linked branches, current_node, mapping, timestamps, roles and limitations of attachment linkage.
5. Recorded privacy risks, local streaming feasibility and a resumable processing plan including the requested {len(plan)}-file selective decompression strategy, exact sizes, disk budget, staging layout, checksums, deduplication and resume rules.
6. Generated and validated the Phase 1 output set.

## Generated files

- `outputs/ARCHIVE_MANIFEST.md`
- `outputs/ARCHIVE_INVENTORY.json`
- `outputs/PRIVACY_RISK_REPORT.md`
- `outputs/PROCESSING_PLAN.md`
- `STATUS.md`
- Supporting reproducible scripts: `scripts/inspect_archive.py`, `scripts/build_phase1_reports.py`, `scripts/verify_phase1.py`.
- Supporting sanitized logs: `outputs/inspection_summary.json`, `outputs/PHASE1_VALIDATION.json`.

## Unresolved questions

Exact conversation/message counts and complete history date range are unknown by design. Attachment-to-message mapping is not verified. {'Some payload formats remain unidentified.' if unidentified_count else 'All payload headers matched a known family.'} Duplicate content has not been byte-verified. Full JSON/ZIP integrity and sync state are unverified. These do not prevent a carefully scoped local metadata-only Phase 2.

## Exact next action

Wait for the archive owner to authorize Phase 2 selective JSON staging and local structural indexing. Resolve any cloud-sync no-transmission concern before any raw staging. After authorization, run the preflight in `outputs/PROCESSING_PLAN.md`; do not begin semantic analysis.

## Safe resume

- Read this file and the privacy report first. Honor the source as strictly read-only and this project as the only write location.
- Verify source path, bytes and mtime against ARCHIVE_INVENTORY.json before any read-intensive work. Re-inventory or ask if source identity changes.
- A Phase 1 rerun is permitted only as a bounded inspection. Use the commands below from the primary project; they replace generated Phase 1 reports only. No source files are modified. If preserving the old snapshot is necessary, version the reports inside the primary project first.
- The inspection script samples at most 64 KiB from {len(d['schema_samples'])} conversation files, at most 2 KiB per ancillary JSON and 64 bytes per opaque payload. It does not extract files. Its tokenizer is not suitable for production parsing.
- No full extraction, full message parsing, classification, embeddings, product/story evaluation or external transmission was performed or is authorized by this status.
- Sync/backup behavior of the source and project locations is unknown. Do not silently move folders or change sync/account settings.

```powershell
python scripts/inspect_archive.py > outputs/inspection_summary.json
python scripts/build_phase1_reports.py
python scripts/verify_phase1.py
```
'''

save('outputs/ARCHIVE_MANIFEST.md',manifest)
save('outputs/PRIVACY_RISK_REPORT.md',privacy)
save('outputs/PROCESSING_PLAN.md',plan_text)
save('STATUS.md',status)
print('Phase 1 reports generated; no source reads or content analysis in report generation.')
