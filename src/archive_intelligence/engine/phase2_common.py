"""Local-only Phase 2 controls. Never log source values or exception text."""
from pathlib import Path
import hashlib, json, os, shutil, zipfile, zlib, datetime
from .engine_config import load_workspace, ConfigError
ROOT=Path(__file__).resolve().parents[3]
# Workspace configuration is the single source of machine-specific values
# (Batch 1/2): expectations.expected_root pins this checkout when configured;
# source.export_zip names the read-only export archive. Defaults are neutral,
# so an unconfigured structurally valid workspace resolves EXPECTED to ROOT.
try:
    _WORKSPACE=load_workspace(root=ROOT)
except ConfigError as _err:
    raise RuntimeError(str(_err)) from None
EXPECTED=_WORKSPACE.expected_root or ROOT
SOURCE=_WORKSPACE.source_export_zip
OUT=ROOT/'outputs'/'phase2'
VERSION='phase2-1.0'
def require(ok,code):
    if not ok: raise RuntimeError(code)
def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()
def write(path,data):
    path=Path(path); require(path.resolve().is_relative_to(ROOT.resolve()),'WRITE_ROOT')
    path.parent.mkdir(parents=True,exist_ok=True)
    p=path.with_name(path.name+'.partial')
    with open(p,'w',encoding='utf-8',newline='\n') as f:
        f.write(data if isinstance(data,str) else json.dumps(data,indent=2,ensure_ascii=True)); f.flush(); os.fsync(f.fileno())
    os.replace(p,path)
def status(stage):
    write(ROOT/'STATUS.md',f'# Archive Intelligence — Phase 2\n\n{stage}\n\nPrimary writable project: `{ROOT}`. Original ZIP remains read-only.\n\nAuthorization: archive-owner authorization on file; all processing local, no media extraction, remote processing or semantic analysis.\nResume: run scripts/phase2_stage.py, then scripts/test_phase2.py, then scripts/phase2_index.py with the bundled Python runtime.\nStop after verified structural indexing.\n')
def require_source():
    """Fail closed with a fixed code when no source archive is configured."""
    require(SOURCE is not None,'SOURCE_NOT_CONFIGURED')
    return SOURCE
def min_free_bytes():
    """Configured workspace disk expectation (expectations.min_free_bytes).

    Returns 0 when not configured; callers decide their own product-level
    fallback floor. Never raises and never logs configuration values.
    """
    try:
        return int(_WORKSPACE.min_free_bytes)
    except Exception:
        return 0
def source_identity():
    source=require_source()
    require(source.is_file(),'SOURCE_MISSING')
    s=source.stat(); return {'bytes':s.st_size,'mtime_ns':s.st_mtime_ns,'sha256':sha(source)}
def directory_identity(infos):
    return digest([[n,hashlib.sha256(i.filename.encode('utf-8')).hexdigest(),i.file_size,i.compress_size,i.CRC,i.compress_type,list(i.date_time),i.header_offset,i.flag_bits] for n,i in enumerate(infos)])
def check_root():
    # Structural contract (Batch 2): supported runtime, workspace identity via
    # the configured expectation pin, no reparse points on the root chain, and
    # the local disk budget. The invocation directory is not part of the
    # contract; every path in the engine resolves from ROOT or configuration.
    try:
        _WORKSPACE.require_supported_python()
    except ConfigError as _err:
        raise RuntimeError(str(_err)) from None
    require(ROOT.resolve()==Path(EXPECTED).resolve(),'WRONG_PROJECT')
    for p in [ROOT,*ROOT.parents]: require(not p.is_symlink() and not (hasattr(p,'is_junction') and p.is_junction()),'REPARSE_ROOT')
    require(shutil.disk_usage(ROOT).free>=2*1024**3,'DISK_BUDGET')
class Lock:
    def __enter__(self):
        OUT.mkdir(parents=True,exist_ok=True)
        self.f=open(OUT/'run.lock','a+b'); self.f.seek(0)
        if self.f.read(1)==b'': self.f.write(b'0'); self.f.flush()
        self.f.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(self.f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(self.f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return self
    def __exit__(self,*args):
        self.f.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(self.f.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(self.f,fcntl.LOCK_UN)
        self.f.close()
