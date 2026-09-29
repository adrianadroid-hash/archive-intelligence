"""Verify derived output consistency and privacy guards using synthetic input."""
from pathlib import Path
import collections, json
from .inspect_archive import schema_sample, safe_name
from .engine_discovery import conversation_members, staging_plan

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs'
d=json.loads((OUT/'ARCHIVE_INVENTORY.json').read_text(encoding='utf-8'))
checks=[]
def check(name,condition):
    checks.append({'check':name,'passed':bool(condition)})
    if not condition: raise AssertionError(name)
check('member count reconciles',len(d['entries'])==d['member_count'])
check('total sizes reconcile',sum(e['uncompressed_bytes'] for e in d['entries'])==d['uncompressed_total_bytes'])
check('extension counts reconcile',sum(x['count'] for x in d['by_extension'].values())==d['file_count'])
# Batch 2: conversation-shard expectations are runtime-derived from the
# inventory itself; no fixed shard counts.
conversations=conversation_members(d)
check('conversation shards present',len(conversations)>=1)
check('conversation shard candidates are distinct',len({(e['uncompressed_bytes'],e['crc32']) for e in conversations})==len(conversations))
n=len(conversations)
expected_samples=len({0,min(1,n-1),min(3,n-1),n-1})
check('bounded conversation samples',len(d['schema_samples'])==expected_samples and all(s['bytes_read']<=65536 for s in d['schema_samples']))
check('bounded ancillary samples',all(s['bytes_read']<=2048 for s in d['ancillary_schema_samples']))
payload_count=sum(1 for e in d['entries'] if e['extension'] in {'.dat','.bundle'} and not e['is_directory'])
encrypted_payload=sum(1 for e in d['entries'] if e['extension'] in {'.dat','.bundle'} and not e['is_directory'] and 'encrypted-entry' in e['risk_flags'])
check('payload header counts reconcile',sum(x['count'] for x in d['opaque_payload_header_scan']['formats'].values())==payload_count-encrypted_payload)
check('no source change recorded',d['source_unchanged_size_and_mtime'])
current=Path(d['source_path']).stat()
check('source identity still matches',current.st_size==d['source_archive_bytes'] and current.st_mtime_ns==d['source_mtime_ns'])
check('no dangerous ZIP path flags',not any('unsafe-extraction-path' in e['risk_flags'] for e in d['entries']))
check('no encrypted members observed',not any('encrypted-entry' in e['risk_flags'] for e in d['entries']))
for filename in ['ARCHIVE_MANIFEST.md','ARCHIVE_INVENTORY.json','PRIVACY_RISK_REPORT.md','PROCESSING_PLAN.md']:
    check('required output: '+filename,(OUT/filename).stat().st_size>0)
check('status exists',(ROOT/'STATUS.md').stat().st_size>0)
check('no staging or Phase 2 output created',not (ROOT/'staging').exists() and not (OUT/'phase2').exists())
# Batch 2: the selective staging plan is the same runtime discovery rule the
# stage step uses (conversation JSON + all ancillary JSON), so this reconciles
# against the inventory rather than a fixed member list or byte total.
plan=staging_plan(d)
selected=[e for e in d['entries'] if e['entry_id'] in {entry['entry_id'] for entry,_ in plan}]
check('selective staging plan reconciles',len(selected)==len(plan) and sum(e['uncompressed_bytes'] for e in selected)==sum(entry['uncompressed_bytes'] for entry,_ in plan))

secret='SYNTHETIC_PRIVATE_VALUE_DO_NOT_EMIT'
node={'id':secret,'parent':None,'message':None}
message={'id':'synthetic-node','parent':secret,'message':{'author':{'role':'user','name':secret},'content':{'content_type':'text','parts':[secret]},'metadata':{'attachments':[{'file_name':secret,'file_id':secret}]}}}
obj=[{'title':secret,'id':secret,'current_node':'synthetic-node','mapping':{secret:node,'synthetic-node':message}}]
s=schema_sample(json.dumps(obj).encode(),{'entry_id':'entry-test','safe_name':'conversations-000.json','uncompressed_bytes':100000})
check('synthetic sensitive values never appear in schema output',secret not in json.dumps(s))
check('synthetic roles and attachment types detected',s['observed_roles']==['user'] and '$[].mapping.*.message.metadata.attachments[].file_id' in s['paths'])
check('synthetic graph resolves',s['bounded_graph_check']['current_node_resolves'] and s['bounded_graph_check']['parent_links_resolve'])
check('unknown filename masked',secret not in safe_name(secret+'.pdf',3)[0])
check('truncated sample handled',schema_sample(b'[{"title":"unfinished',{'entry_id':'test','safe_name':'test','uncompressed_bytes':100})['root_type']=='array')
result={'status':'PASS','checks':checks,'limits':'Checks cover generated metadata and synthetic privacy/shape guards, not full archive integrity or full JSON validation.'}
(OUT/'PHASE1_VALIDATION.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(f'PASS: {len(checks)} Phase 1 consistency and privacy checks. Source size/mtime unchanged. No full parsing performed.')
