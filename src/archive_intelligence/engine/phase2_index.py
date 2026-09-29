"""Deterministic, local metadata-only indexing. All source strings stay in memory."""
from .phase2_common import *
from .phase2_parser import *
from .engine_discovery import shard_files_from_ledger
import sqlite3, collections, time, re
# Public layout adaptation: provenance (P1-1) and fingerprint hashes verify
# the package files beside this module, the public equivalent of the scripts directory.
ENGINE=Path(__file__).resolve().parent
SCHEMA='1.0'
def database(path):
    db=sqlite3.connect(path); db.execute('PRAGMA foreign_keys=ON'); db.execute('PRAGMA temp_store=MEMORY')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS run(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS shards(shard INTEGER PRIMARY KEY,committed INTEGER NOT NULL,status TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS conversations(shard INTEGER,ordinal INTEGER,identity_digest TEXT,structural_digest TEXT,create_time REAL,update_time REAL,current_digest TEXT,metrics_json TEXT,PRIMARY KEY(shard,ordinal));
    CREATE TABLE IF NOT EXISTS nodes(shard INTEGER,conversation_ordinal INTEGER,node_digest TEXT,parent_digest TEXT,message_digest TEXT,is_message INTEGER,role TEXT,content_type TEXT,create_time REAL,update_time REAL,active INTEGER,PRIMARY KEY(shard,conversation_ordinal,node_digest),FOREIGN KEY(shard,conversation_ordinal) REFERENCES conversations(shard,ordinal));
    CREATE TABLE IF NOT EXISTS attachment_refs(shard INTEGER,conversation_ordinal INTEGER,node_digest TEXT,reference_ordinal INTEGER,reference_digest TEXT,field_path_digest TEXT,kind TEXT,resolution TEXT,PRIMARY KEY(shard,conversation_ordinal,node_digest,reference_ordinal),FOREIGN KEY(shard,conversation_ordinal,node_digest) REFERENCES nodes(shard,conversation_ordinal,node_digest));
    CREATE TABLE IF NOT EXISTS attachment_links(shard INTEGER,conversation_ordinal INTEGER,node_digest TEXT,reference_ordinal INTEGER,entry_ordinal INTEGER,method TEXT,PRIMARY KEY(shard,conversation_ordinal,node_digest,reference_ordinal,entry_ordinal),FOREIGN KEY(shard,conversation_ordinal,node_digest,reference_ordinal) REFERENCES attachment_refs(shard,conversation_ordinal,node_digest,reference_ordinal));
    '''); return db
def walkdict(o):
    if isinstance(o,dict):
        yield o
        for v in o.values():yield from walkdict(v)
    elif isinstance(o,list):
        for v in o:yield from walkdict(v)
class Resolver:
    def __init__(self,stage,infos,metadata_paths):
        self.alias=collections.defaultdict(set); self.methods={}; self.payload={n for n,i in enumerate(infos) if Path(i.filename).suffix.lower() in ('.dat','.bundle')}
        self.stats=collections.Counter()
        def put(key,targets,method):
            if isinstance(key,str) and targets:
                self.alias[key].update(targets); self.methods[key]=method
        for n,i in enumerate(infos):put(i.filename,{n},'exact_zip_path')
        # Batch 2: attachment-resolution roles are discovered from staged
        # metadata *content shape* in deterministic staged order, never from
        # fixed member ordinals or filenames, and every role is optional
        # (an absent role contributes zero mappings). Role shapes: alias maps
        # are non-empty string-valued objects, manifest mappings are nested
        # objects carrying "files" lists, file metadata is a list of
        # {file_id,file_name} records. All staged metadata must be valid JSON
        # (structural gate preserved).
        loaded=[(path,strict_load(path.read_bytes())) for path in metadata_paths]
        self.stats['metadata_files_scanned']=len(loaded)
        for _,data in loaded:
            if isinstance(data,dict) and data and all(isinstance(v,str) for v in data.values()):
                for k,v in data.items():
                    put(v,set(self.alias.get(k,())),'explicit_aux_alias')
                self.stats['aux_alias_records']+=len(data)
        for _,data in loaded:
            if isinstance(data,dict):
                for d in walkdict(data):
                    for k,v in d.items():
                        if isinstance(v,dict) and isinstance(v.get('files'),list):
                            targets=set()
                            for f in v['files']:
                                if isinstance(f,str):targets.update(self.alias.get(f,()))
                            put(k,targets,'explicit_manifest_mapping'); self.stats['manifest_mapping_records']+=1
        # File-name joins are candidates only, not guaranteed file-identity matches.
        self.candidates=collections.defaultdict(set)
        for _,data in loaded:
            if not isinstance(data,list):continue
            for r in data:
                if isinstance(r,dict) and isinstance(r.get('file_id'),str) and isinstance(r.get('file_name'),str):
                    self.candidates[r['file_id']].update(self.alias.get(r['file_name'],()))
                    self.stats['file_metadata_records']+=1
    def resolve(self,value):
        keys=[value]
        for prefix in ('sediment://','file-service://','attachment://'):
            if value.startswith(prefix):keys.append(value[len(prefix):])
        targets=set(); methods=set()
        for k in keys:
            targets.update(self.alias.get(k,()));
            if k in self.methods:methods.add(self.methods[k])
        targets &= self.payload
        if len(targets)==1:return 'resolved',targets,'+'.join(sorted(methods))
        if targets:return 'ambiguous',targets,'explicit_mapping_candidates'
        candidates=set()
        for k in keys:candidates.update(self.candidates.get(k,()))
        candidates &= self.payload
        if candidates:return 'candidate_filename_join',candidates,'unverified_filename_join'
        return 'unresolved',set(),'none'
REF_KEYS={'asset_pointer','file_id','attachment_id','image_url','audio_url','video_url'}
def refs(o,path=(),context=False):
    if isinstance(o,dict):
        for k,v in o.items():
            ctx=context or k in ('attachments','files','file','attachment')
            if isinstance(v,str) and (k in REF_KEYS or (context and k in ('id','url','path'))):
                yield path+(k,),k if k in REF_KEYS else 'attachment_field',v
            elif isinstance(v,(dict,list)):
                yield from refs(v,path+(k,),ctx)
    elif isinstance(o,list):
        for n,v in enumerate(o):
            if isinstance(v,(dict,list)):yield from refs(v,path+(n,),context)
def insert_conversation(db,shard,ordinal,c,resolver):
    g=graph(c); m=c['mapping']; rawid=c.get('id') or c.get('conversation_id')
    ident=digest(['conversation',rawid]) if isinstance(rawid,str) else digest(['location',shard,ordinal])
    nd=lambda k:digest([ident,'node',k])
    metrics={'nodes':len(m),'messages':sum(n.get('message') is not None for n in m.values()),'null_nodes':sum(n.get('message') is None for n in m.values()),'roots':len(g['roots']),'branch_points':g['branch_points'],'extra_branches':g['extra_branches'],'leaves':g['leaves'],'active_valid':int(g['active_valid']),'active_nodes':len(g['active']),'alternative_nodes':len(m)-len(g['active']) if g['active_valid'] else 0,'unclassified_path_nodes':0 if g['active_valid'] else len(m),'cycle_nodes':g['cycle_nodes'],'anomalies':g['anomalies']}
    if not isinstance(rawid,str):metrics['anomalies']['missing_conversation_id']=1
    if isinstance(c.get('id'),str) and isinstance(c.get('conversation_id'),str) and c['id']!=c['conversation_id']:metrics['anomalies']['conversation_id_mismatch']=1
    if any('message' not in n for n in m.values()):metrics['anomalies']['missing_message_field']=sum('message' not in n for n in m.values())
    node_rows=[]; ref_rows=[]; link_rows=[]; signature=[]
    for k,n in m.items():
        msg=n.get('message'); ismsg=msg is not None; msg=msg or {}; content=msg.get('content') or {}; author=msg.get('author') or {}
        require(isinstance(content,dict) and isinstance(author,dict),'MESSAGE_CONTENT_SCHEMA')
        role=enum(author.get('role'),ROLES) if ismsg else 'null'; typ=enum(content.get('content_type'),TYPES) if ismsg else 'null'
        mid=digest(['message',msg['id']]) if isinstance(msg.get('id'),str) else None
        ct=timestamp(msg.get('create_time')); ut=timestamp(msg.get('update_time'))
        node_rows.append((shard,ordinal,nd(k),nd(g['parents'][k]) if g['parents'][k] is not None else None,mid,int(ismsg),role,typ,ct,ut,int(k in g['active'])))
        signature.append((nd(k),nd(g['parents'][k]) if g['parents'][k] is not None else None,mid,ismsg,role,typ,ct,ut))
        for ri,(path,kind,value) in enumerate(refs(msg)):
            resolution,targets,method=resolver.resolve(value)
            ref_rows.append((shard,ordinal,nd(k),ri,digest(['reference',value]),digest(path),kind,resolution))
            for target in sorted(targets):link_rows.append((shard,ordinal,nd(k),ri,target,method))
    structural=digest([sorted(signature),c.get('current_node'),timestamp(c.get('create_time')),timestamp(c.get('update_time'))])
    with db:
        db.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?,?,?)',(shard,ordinal,ident,structural,timestamp(c.get('create_time')),timestamp(c.get('update_time')),nd(c['current_node']) if c.get('current_node') is not None else None,json.dumps(metrics,sort_keys=True)))
        db.executemany('INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?,?)',node_rows)
        db.executemany('INSERT INTO attachment_refs VALUES(?,?,?,?,?,?,?,?)',ref_rows)
        db.executemany('INSERT INTO attachment_links VALUES(?,?,?,?,?,?)',link_rows)
        db.execute("INSERT INTO shards VALUES(?,?,'running') ON CONFLICT(shard) DO UPDATE SET committed=excluded.committed,status='running'",(shard,ordinal+1))
    return metrics
def index_shard(db,path,shard,resolver,interrupt_after=None):
    row=db.execute('SELECT committed,status FROM shards WHERE shard=?',(shard,)).fetchone()
    if row and row[1]=='complete':return
    committed=row[0] if row else 0; count=0
    with open(path,'rb') as f:
        for ordinal,c in enumerate(array_items(f)):
            count+=1
            if ordinal<committed:continue
            insert_conversation(db,shard,ordinal,c,resolver)
            if interrupt_after is not None and count==interrupt_after:raise RuntimeError('SYNTHETIC_INTERRUPT')
    require(count>=committed,'CHECKPOINT_PAST_EOF')
    with db:db.execute("INSERT INTO shards VALUES(?,?,'complete') ON CONFLICT(shard) DO UPDATE SET committed=excluded.committed,status='complete'",(shard,count))
def summary(db):
    sums=collections.Counter(); anomalies=collections.Counter()
    for (j,) in db.execute('SELECT metrics_json FROM conversations'):
        m=json.loads(j); anomalies.update(m.pop('anomalies')); sums.update(m)
    counts={'conversations':db.execute('SELECT count(*) FROM conversations').fetchone()[0],**dict(sums)}
    bounds={}
    for table in ('conversations','nodes'):
        for col in ('create_time','update_time'):
            a,b,n=db.execute(f'SELECT min({col}),max({col}),count({col}) FROM {table}').fetchone()
            bounds[table+'_'+col]={'min':datetime.datetime.fromtimestamp(a,datetime.timezone.utc).isoformat() if a is not None else None,'max':datetime.datetime.fromtimestamp(b,datetime.timezone.utc).isoformat() if b is not None else None,'present':n,'missing':db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]-n}
    return {'counts':counts,'anomalies':dict(anomalies),'date_bounds_utc':bounds,'roles':dict(db.execute('SELECT role,count(*) FROM nodes WHERE is_message=1 GROUP BY role')),'content_types':dict(db.execute('SELECT content_type,count(*) FROM nodes WHERE is_message=1 GROUP BY content_type')),'attachments':dict(db.execute('SELECT resolution,count(*) FROM attachment_refs GROUP BY resolution')),'unique_reference_digests':db.execute('SELECT count(DISTINCT reference_digest) FROM attachment_refs').fetchone()[0],'resolved_payload_members':db.execute("SELECT count(DISTINCT l.entry_ordinal) FROM attachment_links l JOIN attachment_refs r USING(shard,conversation_ordinal,node_digest,reference_ordinal) WHERE r.resolution='resolved'").fetchone()[0],'duplicate_id_groups':db.execute('SELECT count(*) FROM (SELECT identity_digest FROM conversations GROUP BY identity_digest HAVING count(*)>1)').fetchone()[0],'duplicate_structural_groups':db.execute('SELECT count(*) FROM (SELECT structural_digest FROM conversations GROUP BY structural_digest HAVING count(*)>1)').fetchone()[0],'conversations_with_branches':db.execute("SELECT count(*) FROM conversations WHERE json_extract(metrics_json,'$.branch_points')>0").fetchone()[0]}
def privacy_audit(db):
    # The schema admits numeric structure, fixed enums and SHA-256 only. No arbitrary
    # source strings can pass this exhaustively checked text-column allowlist.
    hexd=re.compile(r'^[a-f0-9]{64}$'); checks=0
    digests={'conversations':['identity_digest','structural_digest','current_digest'],'nodes':['node_digest','parent_digest','message_digest'],'attachment_refs':['node_digest','reference_digest','field_path_digest'],'attachment_links':['node_digest']}
    for table,cols in digests.items():
        for col in cols:
            for (v,) in db.execute(f'SELECT {col} FROM {table}'):
                require(v is None or bool(hexd.fullmatch(v)),'PRIVACY_DIGEST'); checks+=1
    for col,allowed in [('role',ROLES),('content_type',TYPES)]:
        for (v,) in db.execute(f'SELECT DISTINCT {col} FROM nodes'):
            require(v in allowed|{'null','missing'} or (v.startswith('unknown:') and hexd.fullmatch(v[8:])),'PRIVACY_ENUM')
    for (v,) in db.execute('SELECT metrics_json FROM conversations'):
        obj=json.loads(v)
        require(all(type(x) is int for k,x in obj.items() if k!='anomalies'),'PRIVACY_METRIC')
        require(all(re.fullmatch('[a-z_]+',k) and type(x) is int for k,x in obj['anomalies'].items()),'PRIVACY_ANOMALY')
    require(all(v in REF_KEYS|{'attachment_field'} for (v,) in db.execute('SELECT DISTINCT kind FROM attachment_refs')),'PRIVACY_REF_KIND')
    require(all(v in {'resolved','ambiguous','candidate_filename_join','unresolved'} for (v,) in db.execute('SELECT DISTINCT resolution FROM attachment_refs')),'PRIVACY_RESOLUTION')
    allowed_methods={'exact_zip_path','explicit_aux_alias','explicit_manifest_mapping','explicit_mapping_candidates','unverified_filename_join','none'}
    require(all(set(v.split('+'))<=allowed_methods for (v,) in db.execute('SELECT DISTINCT method FROM attachment_links')),'PRIVACY_METHOD')
    return {'pass':True,'digest_cells_checked':checks,'method':'All persisted text columns constrained to local digests, fixed enums, code-owned keys and numeric structural metrics; no raw bodies or titles in schema.'}
def main():
    check_root()
    with Lock():
        tests=json.loads((OUT/'PARSER_TESTS.json').read_text()); require(tests['pass'],'PARSER_TESTS_REQUIRED')
        # P1-1 (A62): enforce the recorded provenance hashes against the current
        # script files; the pass flag alone is never trusted.
        require(tests.get('parser_sha256')==sha(ENGINE/'phase2_parser.py'),'PARSER_TESTS_PARSER_HASH')
        require(tests.get('index_sha256')==sha(ENGINE/'phase2_index.py'),'PARSER_TESTS_INDEX_HASH')
        stage=ROOT/json.loads((OUT/'staging_pointer.json').read_text())['relative_root']; ledger=json.loads((stage/'extraction_manifest.json').read_text())
        expected=dict(ledger['source']); pre=source_identity()
        require(all(pre[k]==expected[k] for k in pre),'SOURCE_RESUME_CHANGED')
        for r in ledger['files'].values():require(r['status']=='verified' and sha(stage/r['destination'])==r['sha256'],'STAGING_RESUME_CHANGED')
        # Batch 2: metadata-role files and conversation shards are discovered
        # from the verified staging ledger; no fixed ordinals or shard counts.
        metadata_paths=[stage/ledger['files'][eid]['destination'] for eid in sorted(ledger['files']) if ledger['files'][eid]['destination'].startswith('metadata/')]
        shard_files=shard_files_from_ledger(ledger)
        shard_items=list(shard_files.items())
        with zipfile.ZipFile(require_source(),'r') as z:
            infos=z.infolist(); require(directory_identity(infos)==expected['central_directory_sha256'],'DIRECTORY_CHANGED'); resolver=Resolver(stage,infos,metadata_paths)
        db=database(OUT/'structural_index.sqlite')
        fingerprint=digest([SCHEMA,VERSION,expected,sha(ENGINE/'phase2_parser.py'),sha(ENGINE/'phase2_index.py'),sha(ENGINE/'engine_discovery.py')])
        old=db.execute("SELECT value FROM run WHERE key='fingerprint'").fetchone()
        require(old is None or old[0]==fingerprint,'INDEX_VERSION_CHANGED')
        with db:db.execute("INSERT OR IGNORE INTO run VALUES('fingerprint',?)",(fingerprint,))
        timings={}
        for position,(n,(basename,destination,entry_id)) in enumerate(shard_items):
            status(f'Indexing conversation shard {position+1}/{len(shard_items)}; per-conversation SQLite checkpoints active.')
            t=time.monotonic(); index_shard(db,stage/destination,n,resolver); timings[str(n)]=round(time.monotonic()-t,3)
            write(OUT/'run_manifest.json',{'version':VERSION,'schema':SCHEMA,'source':expected,'status':'indexing','shards':list(db.execute('SELECT * FROM shards ORDER BY shard')),'timings_seconds':timings})
            print(f'SHARD_{n}_COMPLETE',flush=True)
            require(shutil.disk_usage(ROOT).free>=2*1024**3,'DISK_BUDGET')
        status(f'All {len(shard_items)} conversation shards indexed. Full structural reconciliation, source rehash and privacy validation in progress.')
        s=summary(db); per=[]; aggregate=collections.Counter()
        for n,(basename,destination,entry_id) in shard_items:
            ct=collections.Counter()
            with open(stage/destination,'rb') as f:
                for c in array_items(f):
                    ct['conversations']+=1; ct['nodes']+=len(c['mapping']); ct['messages']+=sum(x.get('message') is not None for x in c['mapping'].values())
            actual={'conversations':db.execute('SELECT count(*) FROM conversations WHERE shard=?',(n,)).fetchone()[0],'nodes':db.execute('SELECT count(*) FROM nodes WHERE shard=?',(n,)).fetchone()[0],'messages':db.execute('SELECT coalesce(sum(is_message),0) FROM nodes WHERE shard=?',(n,)).fetchone()[0]}
            require(dict(ct)==actual,'SHARD_RECONCILIATION'); per.append({'shard':n,'array_eof':True,**actual}); aggregate.update(actual)
        require(all(s['counts'][k]==v for k,v in aggregate.items()),'TOTAL_RECONCILIATION')
        require(sum(s['roles'].values())==s['counts']['messages'] and sum(s['content_types'].values())==s['counts']['messages'],'ENUM_RECONCILIATION')
        require(s['counts']['messages']+s['counts']['null_nodes']==s['counts']['nodes'],'NULL_RECONCILIATION')
        require(db.execute('PRAGMA integrity_check').fetchall()==[('ok',)],'SQLITE_INTEGRITY')
        require(not db.execute('PRAGMA foreign_key_check').fetchall(),'SQLITE_FOREIGN_KEYS')
        privacy=privacy_audit(db)
        for r in ledger['files'].values():
            path=stage/r['destination']; require(path.stat().st_size==r['bytes'] and sha(path)==r['sha256'],'STAGED_FINAL_HASH')
        after=source_identity(); require(all(after[k]==expected[k] for k in after),'SOURCE_FINAL_CHANGED')
        with zipfile.ZipFile(require_source(),'r') as z:require(directory_identity(z.infolist())==expected['central_directory_sha256'],'DIRECTORY_FINAL_CHANGED')
        actual_files={str(p.relative_to(stage)).replace('\\','/') for p in stage.rglob('*') if p.is_file()}
        expected_files={r['destination'] for r in ledger['files'].values()}|{'extraction_manifest.json'}
        require(actual_files==expected_files,'UNEXPECTED_STAGED_FILE')
        require(all(p.resolve().is_relative_to(ROOT.resolve()) for p in ROOT.rglob('*')),'GENERATED_PATH_ESCAPE')
        validation={'pass':True,'per_shard':per,'counts':s['counts'],'staged_hashes_revalidated':len(ledger['files']),'source_hash_size_mtime_revalidated':True,'central_directory_revalidated':True,'sqlite_integrity':'ok','foreign_keys':'ok','privacy':privacy,'no_media_extracted':True,'all_generated_paths_local':True,'parser_tests':tests,'graph_anomalies':s['anomalies'],'structural_validity':'valid' if not s['anomalies'] else 'index_valid_with_source_graph_anomalies','limitations':['Attachment counts cover explicit structured reference fields, not links embedded in prose.','Filename-only joins remain unverified candidates. Ambiguous mappings are not counted as resolved.','No content-equivalence or semantic duplicate detection; ID and structural digest comparison only.','Missing timestamps are reported; none are inferred.','No assertion is made about pre-existing source storage synchronization or absence of secrets inside raw staged JSON.']}
        write(OUT/'VALIDATION.json',validation)
        write(OUT/'run_manifest.json',{'version':VERSION,'schema':SCHEMA,'fingerprint':fingerprint,'source':expected,'status':'verified_complete','shards':list(db.execute('SELECT * FROM shards ORDER BY shard')),'per_shard':per,'timings_seconds':timings,'metadata_mapping_stats':dict(resolver.stats),'summary':s,'script_sha256':{p.name:sha(p) for p in ENGINE.glob('phase2_*.py')},'completed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
        write(OUT/'STRUCTURAL_SUMMARY.md',f'# Phase 2 structural summary\n\nVerified local structural index; all {len(shard_items)} conversation arrays completed and EOF validated twice. All branches retained.\n\n```json\n'+json.dumps(s,indent=2)+'\n```\n\nDates are UTC Unix-second fields, with fractional precision preserved. Nodes include null structural nodes; messages exclude null nodes. Active nodes follow current_node back to a root. Alternative nodes are all nodes outside a valid active path, including sibling revisions. Extra branches sum max(children minus one, zero). Graph anomalies are retained, never repaired silently.\n\n'+ '\n'.join('- '+x for x in validation['limitations'])+'\n')
        write(OUT/'ATTACHMENT_LINKAGE_SUMMARY.md','# Attachment linkage\n\n'+json.dumps({'reference_occurrences':sum(s['attachments'].values()),'unique_references':s['unique_reference_digests'],'results':s['attachments'],'unique_resolved_payload_members':s['resolved_payload_members'],'metadata_stats':dict(resolver.stats)},indent=2)+'\n\nReference occurrences are structural field occurrences, so one asset may occur multiple times. Exact ZIP paths and explicit auxiliary/manifest mappings establish resolved links only when a single payload remains. File-metadata filename joins are candidates, even if unique. Multi-target mappings remain ambiguous. Reference and node digests plus ZIP ordinals preserve all candidate links. No media was opened or extracted. Prose links, citations and arbitrary strings were not interpreted.\n')
        write(OUT/'DUPLICATE_ASSESSMENT.md',f"# Duplicate assessment\n\nRepeated conversation identity groups: {s['duplicate_id_groups']}.\nRepeated structural digest groups: {s['duplicate_structural_groups']}.\n\nEvery source occurrence is retained by shard and ordinal. Structural digest includes digested node/message identities, parent links, roles/types and timestamps; it is not proof of message-body equivalence. No content hashing or semantic similarity analysis was performed. Media size/CRC candidates from Phase 1 remain unverified; no media deduplication was attempted.\n")
        db.close(); status('PHASE 2 VERIFIED COMPLETE — STOPPED AFTER STRUCTURAL INDEXING.\n\nCounts and findings: outputs/phase2/STRUCTURAL_SUMMARY.md. Validation: outputs/phase2/VALIDATION.json.\n\nNext action: obtain a separate Phase 3 authorization specifying purpose, permitted conversations/fields and privacy rules before any semantic analysis. No Phase 3 work has begun.')
        print(json.dumps({'result':'VERIFIED_COMPLETE','summary':s}))
if __name__=='__main__':
    try:main()
    except Exception as e:
        # Exception messages/tracebacks may include source data. Print safe type only.
        code=str(e) if isinstance(e,RuntimeError) and re.fullmatch('[A-Z_]+',str(e)) else type(e).__name__
        write(OUT/'safe_error.json',{'code':code}); status('Phase 2 stopped on safe error code '+code+'. Existing transaction checkpoints preserved.')
        print('INDEX_FAILED '+code); raise SystemExit(1)
