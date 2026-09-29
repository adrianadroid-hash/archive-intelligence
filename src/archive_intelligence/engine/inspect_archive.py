"""Phase 1 only: read ZIP directory and bounded schema samples; never extract.

All persisted output stays under the primary project. No network operations.
Unknown filenames/paths are represented by ZIP ordinal and SHA-256 fingerprint.
"""
from pathlib import Path
import collections, datetime, hashlib, json, re, shutil, zipfile

ROOT = Path(__file__).resolve().parents[3]
SAMPLE_LIMIT = 65536

def configured_workspace():
    """Resolve source/output locations from the configuration layer.

    No machine-specific literal: the source directory (user-supplied input)
    and the outputs directory (derived workspace path) come from
    archive_intelligence.engine.engine_config. See engine_config.example.json
    for the schema.
    """
    from .engine_config import load_workspace
    return load_workspace(root=ROOT)

def safe_name(name, ordinal):
    base = name.replace('\\', '/').rsplit('/', 1)[-1]
    allowed = re.fullmatch(r'(?:conversations?(?:[-_]?\d+)?|chat|user|message_feedback|model_comparisons|shared_conversations|group_chats|shopping|sora|settings|files|attachments|media|manifest|index|export_metadata|asset_manifest)\.(?:json|html)', base, re.I)
    ext = Path(base).suffix.lower()
    ext = ext if re.fullmatch(r'\.[a-z0-9]{1,10}', ext) else '[other]'
    return (base if allowed else f'entry-{ordinal:06d}{ext}'), ext

def inventory(workspace=None):
    ws = workspace if workspace is not None else configured_workspace()
    candidates = list(ws.require_source_dir().iterdir())
    if len(candidates) != 1 or not candidates[0].is_file() or candidates[0].suffix.lower() != '.zip':
        raise RuntimeError('Source ambiguity or type changed; stop and ask user.')
    source = candidates[0]
    before = source.stat()
    usage = shutil.disk_usage(ws.root)
    records, groups, duplicate_signatures = [], {}, collections.defaultdict(list)
    risk_exts = {'.pem','.key','.p12','.pfx','.env','.kdbx','.ovpn','.sqlite','.db','.csv','.txt','.html','.json','.pdf','.docx','.xlsx'}
    media_exts = {'.png','.jpg','.jpeg','.webp','.gif','.bmp','.heic','.svg','.mp3','.wav','.m4a','.ogg','.mp4','.mov','.webm','.aac','.flac'}
    attachments_exts = {'.pdf','.doc','.docx','.xls','.xlsx','.csv','.tsv','.ppt','.pptx','.txt','.zip','.7z','.rar'}
    with zipfile.ZipFile(source, 'r') as z:
        infos = z.infolist()
        for ordinal, i in enumerate(infos):
            display, ext = safe_name(i.filename, ordinal)
            parts = i.filename.replace('\\','/').split('/')
            is_conversation = bool(re.fullmatch(r'conversations?(?:[-_]?\d+)?\.json', parts[-1], re.I))
            risks = []
            if ext in risk_exts: risks.append('potential-private-content-container')
            if re.search(r'password|secret|credential|token|backup.?code|recovery|auth|private.?key|\.env(?:\.|$)', i.filename, re.I): risks.append('sensitive-name-pattern')
            unsafe = i.filename.startswith(('/', '\\')) or '..' in parts or bool(re.match(r'^[A-Za-z]:', i.filename))
            if unsafe: risks.append('unsafe-extraction-path')
            if i.flag_bits & 1: risks.append('encrypted-entry')
            r = dict(entry_id=f'entry-{ordinal:06d}', zip_directory_index=ordinal, safe_name=display,
                     path_sha256=hashlib.sha256(i.filename.encode('utf-8')).hexdigest(), extension=ext,
                     is_directory=i.is_dir(), uncompressed_bytes=i.file_size, compressed_bytes=i.compress_size,
                     zip_timestamp=list(i.date_time), crc32=f'{i.CRC:08x}', compression_method=i.compress_type,
                     is_conversation_json=is_conversation, is_media=ext in media_exts,
                     is_other_attachment_candidate=ext in attachments_exts, risk_flags=risks)
            records.append(r)
            if not i.is_dir():
                g = groups.setdefault(ext, dict(count=0, uncompressed_bytes=0, compressed_bytes=0))
                for key, amount in [('count',1),('uncompressed_bytes',i.file_size),('compressed_bytes',i.compress_size)]: g[key]+=amount
                duplicate_signatures[(i.file_size,i.CRC)].append(r['entry_id'])
        sampled = []
        conversations = [r for r in records if r['is_conversation_json']]
        selected = [conversations[n] for n in sorted(set([0, min(1,len(conversations)-1), min(3,len(conversations)-1), len(conversations)-1]))] if conversations else []
        for r in selected:
            with z.open(infos[r['zip_directory_index']], 'r') as stream:
                raw = stream.read(SAMPLE_LIMIT)
            sampled.append(schema_sample(raw, r))
        ancillary_samples=[]
        for r in records:
            if r['extension']!='.json' or r['is_conversation_json']: continue
            with z.open(infos[r['zip_directory_index']], 'r') as stream:
                raw=stream.read(2048)
            sample=schema_sample(raw,r)
            ancillary_samples.append({k:sample[k] for k in ['entry_id','safe_name','bytes_read','root_type','paths']})
        # Read at most 64 bytes per opaque payload to identify formats, not content.
        signature_counts = collections.Counter()
        signature_bytes = collections.Counter()
        header_bytes_read = 0
        for r in records:
            if r['extension'] not in {'.dat','.bundle'} or r['is_directory']: continue
            if 'encrypted-entry' in r['risk_flags']:
                r['header_format']='encrypted-not-read'; continue
            with z.open(infos[r['zip_directory_index']], 'r') as stream:
                header=stream.read(64)
            header_bytes_read+=len(header)
            fmt=header_format(header)
            r['header_format']=fmt
            signature_counts[fmt]+=1
            signature_bytes[fmt]+=r['uncompressed_bytes']
        duplicate_names = collections.Counter(i.filename for i in infos)
        duplicate_name_groups=[[r['entry_id'] for r in records if r['path_sha256']==hashlib.sha256(name.encode('utf-8')).hexdigest()] for name,count in duplicate_names.items() if count>1]
    after = source.stat()
    if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
        raise RuntimeError('Source changed during inspection; do not trust snapshot.')
    result = dict(phase=1, inspected_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        source_path=str(source), source_type='ZIP archive', source_container_count=1,
        source_archive_bytes=before.st_size, source_mtime_ns=before.st_mtime_ns,
        source_unchanged_size_and_mtime=True, disk_free_bytes_before=usage.free, disk_total_bytes=usage.total,
        member_count=len(records), file_count=sum(not r['is_directory'] for r in records),
        uncompressed_total_bytes=sum(r['uncompressed_bytes'] for r in records),
        compressed_members_total_bytes=sum(r['compressed_bytes'] for r in records),
        by_extension=groups, conversation_files=conversations,
        largest_files=sorted((r for r in records if not r['is_directory']), key=lambda r:r['uncompressed_bytes'], reverse=True)[:15],
        extension_only_media_count=sum(r['is_media'] for r in records),
        extension_only_media_uncompressed_bytes=sum(r['uncompressed_bytes'] for r in records if r['is_media']),
        extension_only_other_attachment_candidate_count=sum(r['is_other_attachment_candidate'] for r in records),
        extension_only_other_attachment_candidate_bytes=sum(r['uncompressed_bytes'] for r in records if r['is_other_attachment_candidate']),
        opaque_payload_header_scan={'limit_bytes_per_entry':64,'total_bytes_read':header_bytes_read,
            'formats':{f:{'count':c,'uncompressed_bytes':signature_bytes[f]} for f,c in signature_counts.items()}},
        duplicate_member_name_groups=duplicate_name_groups,
        zip_date_min=list(min(i.date_time for i in infos)), zip_date_max=list(max(i.date_time for i in infos)),
        possible_duplicate_member_groups=[v for (size,crc),v in duplicate_signatures.items() if len(v)>1 and size>0],
        schema_samples=sampled, ancillary_schema_samples=ancillary_samples, entries=records,
        limitations=['No full ZIP integrity test or full-source hash; only size/mtime unchanged check.',
            'Size+CRC duplicate candidates are not byte-verified duplicates.',
            'ZIP dates are packaging metadata, not conversation date range.',
            'Media signatures are header-only identifications, not full decoding; ISO-BMFF subtypes unresolved.',
            'No message bodies, titles, author names, raw node IDs or private paths are persisted.',
            'Only bounded JSON prefixes inspected; unobserved fields are not proven absent.'])
    out_dir = ws.outputs_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / 'ARCHIVE_INVENTORY.json'
    temp = target.with_suffix('.json.tmp')
    temp.write_text(json.dumps(result,ensure_ascii=False,indent=2), encoding='utf-8')
    temp.replace(target)
    summary = {k:v for k,v in result.items() if k not in {'entries','largest_files','conversation_files','possible_duplicate_member_groups','ancillary_schema_samples'}}
    summary['conversation_files'] = [{k:r[k] for k in ['entry_id','safe_name','uncompressed_bytes','compressed_bytes']} for r in conversations]
    summary['duplicate_candidate_group_count'] = len(result['possible_duplicate_member_groups'])
    summary['schema_samples']=[{k:v for k,v in s.items() if k not in {'paths','sampled_timestamps'}} for s in sampled]
    summary['largest_files']=[{k:r[k] for k in ['entry_id','safe_name','uncompressed_bytes']} for r in result['largest_files']]
    print(json.dumps(summary,ensure_ascii=False,indent=2))

def header_format(h):
    if h.startswith(b'\x89PNG\r\n\x1a\n'): return 'PNG'
    if h.startswith(b'\xff\xd8\xff'): return 'JPEG'
    if h.startswith((b'GIF87a',b'GIF89a')): return 'GIF'
    if h.startswith(b'RIFF') and h[8:12]==b'WEBP': return 'WebP'
    if h.startswith(b'RIFF') and h[8:12]==b'WAVE': return 'WAV'
    if h.startswith(b'RIFF') and h[8:12]==b'AVI ': return 'AVI'
    if h.startswith(b'OggS'): return 'Ogg container'
    if h.startswith(b'fLaC'): return 'FLAC'
    if h.startswith(b'ID3'): return 'ID3 audio candidate'
    if len(h)>1 and h[0]==255 and h[1]&224==224: return 'MPEG audio candidate'
    if h[4:8]==b'ftyp': return 'ISO-BMFF media container'
    if h.startswith(b'\x1aE\xdf\xa3'): return 'Matroska/WebM container'
    if h.startswith(b'%PDF-'): return 'PDF'
    if h.startswith(b'PK\x03\x04'): return 'ZIP-family container'
    if h.startswith(b'SQLite format 3\x00'): return 'SQLite'
    if h.startswith(b'\xd0\xcf\x11\xe0'): return 'OLE compound document'
    if h.startswith(b'\x1f\x8b'): return 'gzip container'
    return 'unidentified'

# Strict, bounded structural tokenizer. Strings are decoded transiently; no message
# values are logged. A prefix can end mid-token/container without becoming an error.
SAFE_KEYS = set('id title create_time update_time mapping current_node message parent children author role name metadata recipient channel content content_type parts status end_turn weight attachments asset_pointer image_asset_pointer audio_asset_pointer video_asset_pointer width height size mime_type file_id file_name filename url is_visually_hidden_from_conversation conversation_id gizmo_id is_archived is_starred default_model_slug model_slug finish_details type citations content_references request_id timestamp start_time end_time files assets data mimeType original_name path asset_id file_size conversations version export_date sha256 sha1 mime content_hash original_filename file_path relative_path local_path attachment_id'.split())
SAFE_ENUMS = {'role': {'system','user','assistant','tool','developer'}, 'content_type': {'text','multimodal_text','image_asset_pointer','audio','audio_perception','audio_text','video','code','execution_output','tether_browsing_display','system_error'}}

def schema_sample(raw, record):
    text = raw.decode('utf-8-sig',errors='ignore')
    decoder = json.JSONDecoder()
    pos, stack, observations = 0, [], {}
    roles, ctypes, timestamps = set(), set(), []
    containers_completed = 0
    def value_path():
        if not stack: return '$'
        f=stack[-1]
        if f['kind']=='array': return f['path']+'[]'
        key=f.get('key','[unknown-key]')
        if f['path'].endswith('.mapping'): key='*'
        elif key not in SAFE_KEYS: key='[other-key]'
        return f['path']+'.'+key
    def observe(path, typ, value=None):
        observations.setdefault(path,set()).add(typ)
        key=path.rsplit('.',1)[-1]
        if key in SAFE_ENUMS and isinstance(value,str) and value in SAFE_ENUMS[key]:
            (roles if key=='role' else ctypes).add(value)
        if key in {'create_time','update_time','timestamp'} and isinstance(value,(float,int)) and 946684800<value<4102444800:
            timestamps.append({'field':key,'utc':datetime.datetime.fromtimestamp(value,datetime.timezone.utc).isoformat()})
    def consumed():
        if stack: stack[-1]['expect']='comma'
    while pos<len(text):
        if text[pos].isspace(): pos+=1; continue
        ch=text[pos]
        if ch in '{[':
            p=value_path(); kind='object' if ch=='{' else 'array'; observe(p,kind)
            consumed(); stack.append({'kind':kind,'path':p,'expect':'key' if kind=='object' else 'value'}); pos+=1
        elif ch in '}]':
            if not stack: break
            stack.pop(); containers_completed+=1; pos+=1
        elif ch==':':
            if stack: stack[-1]['expect']='value'
            pos+=1
        elif ch==',':
            if stack: stack[-1]['expect']='key' if stack[-1]['kind']=='object' else 'value'
            pos+=1
        else:
            try: value,end=decoder.raw_decode(text,pos)
            except (ValueError,json.JSONDecodeError): break
            if end==len(text) and isinstance(value,(int,float)): break
            if stack and stack[-1]['kind']=='object' and stack[-1]['expect']=='key':
                stack[-1]['key']=value if isinstance(value,str) else '[unknown-key]'
            else:
                typ='null' if value is None else 'boolean' if isinstance(value,bool) else 'string' if isinstance(value,str) else 'number'
                observe(value_path(),typ,value)
                consumed()
            pos=end
    graph={ 'status':'First conversation does not fit within bounded sample.' }
    try:
        offset=text.index('[')+1
        while text[offset].isspace(): offset+=1
        obj,_=decoder.raw_decode(text,offset)
        if isinstance(obj,dict) and isinstance(obj.get('mapping'),dict):
            mapping=obj['mapping']; nodes=[n for n in mapping.values() if isinstance(n,dict)]
            parents=collections.Counter(n.get('parent') for n in nodes if isinstance(n.get('parent'),str))
            graph=dict(status='One complete conversation within prefix, structural counts only.',
                node_count=len(nodes), null_message_nodes=sum(n.get('message') is None for n in nodes),
                nodes_with_children_field=sum('children' in n for n in nodes),
                nodes_with_parent_field=sum('parent' in n for n in nodes),
                current_node_resolves=obj.get('current_node') in mapping,
                parent_links_resolve=all(n.get('parent') is None or n.get('parent') in mapping for n in nodes),
                parents_with_multiple_children=sum(c>1 for c in parents.values()))
    except (ValueError,IndexError,TypeError): pass
    date_values=[x['utc'] for x in timestamps]
    return dict(entry_id=record['entry_id'], safe_name=record['safe_name'], bytes_read=len(raw),
        limit_bytes=SAMPLE_LIMIT, entire_member_read=len(raw)>=record['uncompressed_bytes'],
        root_type='array' if text.lstrip().startswith('[') else 'object' if text.lstrip().startswith('{') else 'unknown',
        paths={p:sorted(t) for p,t in sorted(observations.items())}, observed_roles=sorted(roles),
        observed_content_types=sorted(ctypes), sampled_timestamps=timestamps[:30],
        sampled_timestamp_min=min(date_values) if date_values else None,
        sampled_timestamp_max=max(date_values) if date_values else None, bounded_graph_check=graph,
        parsed_prefix_characters=pos, note='Only types, approved role/content enums and numeric timestamps retained. No content strings retained.')

if __name__ == '__main__': inventory()
