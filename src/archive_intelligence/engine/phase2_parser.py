"""Strict incremental array framing, standard-library JSON decoding, bounded objects."""
import json, math
from .phase2_common import require,digest
MAX_ELEMENT=64*1024*1024
MAX_DEPTH=256
def pairs(items):
    out={}
    for k,v in items:
        require(k not in out,'JSON_DUPLICATE_KEY'); out[k]=v
    return out
def bad_constant(v): raise ValueError('JSON_NONFINITE')
def strict_load(raw):
    try: return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=bad_constant)
    except (UnicodeError,json.JSONDecodeError): raise ValueError('JSON_INVALID') from None
def array_items(stream,chunk_size=65536):
    """Byte framing preserves split UTF-8; each complete element receives strict decoding.
    A maximum-sized element, not an entire shard, is held in memory. No disk spills.
    """
    state='start'; buf=bytearray(); depth=0; quoted=False; escaped=False; offset=0
    for chunk in iter(lambda:stream.read(chunk_size),b''):
        for b in chunk:
            offset+=1
            if state=='done': require(b in b' \r\n\t','JSON_TRAILING'); continue
            if state=='start':
                if b in b' \r\n\t': continue
                require(b==91,'JSON_ARRAY_REQUIRED'); state='first'; continue
            if state in ('first','next'):
                if b in b' \r\n\t': continue
                if b==93:
                    require(state=='first','JSON_TRAILING_COMMA'); state='done'; continue
                state='value'
            if not quoted and depth==0 and b in (44,93):
                require(bool(buf.strip()),'JSON_EMPTY_ELEMENT')
                value=strict_load(bytes(buf)); buf.clear()
                state='next' if b==44 else 'done'
                yield value
                continue
            buf.append(b); require(len(buf)<=MAX_ELEMENT,'JSON_ELEMENT_LIMIT')
            if quoted:
                if escaped: escaped=False
                elif b==92: escaped=True
                elif b==34: quoted=False
            elif b==34: quoted=True
            elif b in (91,123):
                depth+=1; require(depth<=MAX_DEPTH,'JSON_DEPTH_LIMIT')
            elif b in (93,125):
                depth-=1; require(depth>=0,'JSON_BRACKET')
    require(state=='done' and not buf,'JSON_INCOMPLETE')
def timestamp(v):
    if v is None:return None
    require(type(v) in (float,int) and math.isfinite(v) and -62135596800<=v<=253402300799,'TIMESTAMP_FORMAT')
    return float(v)
ROLES={'system','developer','user','assistant','tool','function'}
TYPES={'text','multimodal_text','image','audio','audio_asset_pointer','input_audio','input_audio_transcription','audio_transcription','text_audio','video','video_frame','real_time_user_audio_video','computer_output','execution_output','tether_browsing_display','code','reasoning_recap','thoughts','app_pairing_content','refusal','review','notification','juicepred'}
def enum(v,allowed):
    if v is None:return 'missing'
    return v if isinstance(v,str) and v in allowed else 'unknown:'+digest(v)
def graph(c):
    require(isinstance(c,dict) and isinstance(c.get('mapping'),dict),'CONVERSATION_SCHEMA')
    m=c['mapping']; parents={}; children={k:[] for k in m}; anomalies={}; roots=[]
    def add(code,n=1): anomalies[code]=anomalies.get(code,0)+n
    for k,n in m.items():
        require(isinstance(n,dict),'NODE_SCHEMA'); p=n.get('parent'); parents[k]=p
        if n.get('id')!=k:add('node_id_mismatch')
        if 'parent' not in n:add('missing_parent_field')
        if p is None: roots.append(k)
        elif not isinstance(p,str):add('invalid_parent_type')
        elif p not in m:add('missing_parent')
        else:children[p].append(k)
        require(n.get('message') is None or isinstance(n.get('message'),dict),'MESSAGE_SCHEMA')
    if len(roots)>1:add('multiple_roots')
    if not roots:add('no_root')
    for k,n in m.items():
        if 'children' in n:
            q=n['children']
            if not isinstance(q,list) or any(not isinstance(v,str) for v in q):add('invalid_children')
            elif len(q)!=len(set(q)) or set(q)!=set(children[k]):add('children_mismatch')
    visited=set(); cycles=0; cycle_nodes=0
    for k in m:
        if k in visited:continue
        trail=[]; pos={}; cur=k
        while isinstance(cur,str) and cur in m and cur not in visited and cur not in pos:
            pos[cur]=len(trail); trail.append(cur); cur=parents[cur]
        if isinstance(cur,str) and cur in pos: cycles+=1; cycle_nodes+=len(trail)-pos[cur]
        visited.update(trail)
    if cycles:add('cycles',cycles)
    current=c.get('current_node'); active=[]; valid=False
    if current is None:add('missing_current_node')
    elif not isinstance(current,str) or current not in m:add('invalid_current_node')
    else:
        seen=set(); cur=current
        while isinstance(cur,str) and cur in m and cur not in seen:
            seen.add(cur); active.append(cur); cur=parents[cur]
        valid=cur is None
        if not valid:add('invalid_active_path')
    if not valid:active=[]
    return {'parents':parents,'children':children,'roots':roots,'active':set(active),'active_valid':valid,'anomalies':anomalies,'cycles':cycles,'cycle_nodes':cycle_nodes,'branch_points':sum(len(v)>1 for v in children.values()),'extra_branches':sum(max(0,len(v)-1) for v in children.values()),'leaves':sum(not v for v in children.values())}
