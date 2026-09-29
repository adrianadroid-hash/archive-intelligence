from .phase2_common import *
from .engine_discovery import staging_plan
def main():
    check_root()
    source=require_source()
    with Lock():
        inv=json.loads((ROOT/'outputs/ARCHIVE_INVENTORY.json').read_text(encoding='utf-8'))
        require(str(source)==inv['source_path'],'SOURCE_PATH')
        st=source.stat(); require((st.st_size,st.st_mtime_ns)==(inv['source_archive_bytes'],inv['source_mtime_ns']),'SOURCE_STAT')
        identity=source_identity(); require(source.stat().st_mtime_ns==st.st_mtime_ns,'SOURCE_CHANGED')
        stage=ROOT/'staging'/identity['sha256']; mp=stage/'extraction_manifest.json'
        with zipfile.ZipFile(source,'r') as z:
            infos=z.infolist(); require(len(infos)==inv['member_count'],'DIRECTORY_COUNT')
            for r,i in zip(inv['entries'],infos):
                require(hashlib.sha256(i.filename.encode()).hexdigest()==r['path_sha256'] and i.file_size==r['uncompressed_bytes'] and i.compress_size==r['compressed_bytes'] and f'{i.CRC:08x}'==r['crc32'] and list(i.date_time)==r['zip_timestamp'] and i.compress_type==r['compression_method'],'DIRECTORY_IDENTITY')
            identity['central_directory_sha256']=directory_identity(infos)
            manifest=json.loads(mp.read_text()) if mp.exists() else {'version':VERSION,'source':identity,'files':{}}
            require(manifest['source']==identity and manifest['version']==VERSION,'RESUME_IDENTITY')
            write(OUT/'preflight.json',{'source':identity,'source_path_verified':True,'local_root_verified':True,'free_bytes':shutil.disk_usage(ROOT).free,'inventory_entries_verified':len(infos),'dependencies':'Python standard library only'})
            # Batch 2: which members stage is decided at runtime from the
            # verified Phase 1 inventory (conversation JSON + all ancillary
            # JSON); no fixed member ordinals, shard counts or byte totals.
            plan=staging_plan(inv)
            plan_ids=[entry['entry_id'] for entry,_ in plan]
            expected_total=sum(entry['uncompressed_bytes'] for entry,_ in plan)
            conversation_count=sum(1 for _,rel in plan if rel.startswith('conversations/'))
            metadata_count=len(plan)-conversation_count
            status(f'Preflight passed. Selective staging of {len(plan)} JSON members ({conversation_count} conversation, {metadata_count} ancillary) in progress.')
            for entry,rel in plan:
                require(shutil.disk_usage(ROOT).free>=2*1024**3,'DISK_BUDGET')
                n=entry['zip_directory_index']; i=infos[n]; eid=entry['entry_id']
                dest=stage/rel; require(dest.resolve().is_relative_to(stage.resolve()),'STAGE_PATH')
                r=manifest['files'].get(eid,{})
                if r.get('status')=='verified':
                    require(r.get('destination')==rel and r.get('entry_ordinal')==n,'STAGED_DESTINATION')
                    require(dest.exists() and dest.stat().st_size==r['bytes'] and sha(dest)==r['sha256'],'STAGED_HASH'); continue
                r={'status':'extracting','destination':rel,'entry_ordinal':n,'path_sha256':entry['path_sha256'],'expected_bytes':i.file_size,'expected_crc32':f'{i.CRC:08x}'}
                manifest['files'][eid]=r; write(mp,manifest)
                dest.parent.mkdir(parents=True,exist_ok=True); partial=dest.with_name(dest.name+'.partial')
                h=hashlib.sha256(); count=0; crc=0
                # Existing final after a crash is adopted only after verifying a fresh source read.
                output=None if dest.exists() else open(partial,'wb')
                try:
                    with z.open(i,'r') as src:
                        for b in iter(lambda:src.read(1024*1024),b''):
                            h.update(b); crc=zlib.crc32(b,crc); count+=len(b)
                            if output: output.write(b)
                    if output: output.flush(); os.fsync(output.fileno())
                finally:
                    if output: output.close()
                require(count==i.file_size and crc==i.CRC,'MEMBER_CHECKSUM')
                require(sha(dest if dest.exists() else partial)==h.hexdigest(),'DEST_CHECKSUM')
                if not dest.exists(): os.replace(partial,dest)
                r.update(status='verified',bytes=count,crc32=f'{crc:08x}',sha256=h.hexdigest()); write(mp,manifest)
            require(set(manifest['files'])==set(plan_ids),'STAGING_PLAN_CHANGED')
            require(sum(r['bytes'] for r in manifest['files'].values())==expected_total,'STAGING_TOTAL')
            write(OUT/'staging_pointer.json',{'relative_root':str(stage.relative_to(ROOT))})
            status(f'Selective staging complete: {len(plan)} JSON files ({conversation_count} conversation, {metadata_count} ancillary) verified by member identity, bytes, CRC and SHA-256. Parser testing and indexing pending.')
            print(f'PREFLIGHT_AND_STAGING_PASS files={len(plan)} bytes={expected_total}')
if __name__=='__main__':
    try: main()
    except Exception as e:
        print('PHASE2_STAGE_FAILED type='+type(e).__name__); raise SystemExit(1)
