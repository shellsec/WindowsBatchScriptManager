"""Task creation, environment discovery and deterministic queue primitives."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

FEATURE_FIELDS = ('success_exit_codes', 'on_success', 'on_failure', 'environment_kind', 'environment_root')

def migrate_items(items):
    reserved = {i['id'] for i in items if isinstance(i.get('id'), int) and i['id'] > 0}
    seen = set(); result = []; next_id = max(reserved, default=0) + 1
    for index, item in enumerate(items, 1):
        item = dict(item); ident = item.get('id')
        if not isinstance(ident, int) or ident <= 0 or ident in seen:
            ident = index if index not in reserved and index not in seen else next_id
            next_id = max(next_id, ident + 1)
        seen.add(ident); item['id'] = ident
        result.append(item)
    return result

def parse_codes(value):
    parts = value if isinstance(value, (list,tuple)) else str(value).split(',')
    if not parts or any(not str(p).strip() for p in parts):
        raise ValueError('请输入逗号分隔的整数退出码 / Enter integer exit codes')
    return list(dict.fromkeys(int(str(p).strip()) for p in parts))

def link_ids(value):
    if not value: return []
    parts = value if isinstance(value,(list,tuple)) else str(value).split(',')
    return list(dict.fromkeys(int(str(p).strip()) for p in parts if str(p).strip()))

def validate_links(items, *, start_id=None):
    """Validate the whole configuration, or just the tasks reachable from a run."""
    by_id = {i['id']: i for i in items}
    if start_id is not None:
        related = {}
        pending = [start_id]
        while pending:
            ident = pending.pop()
            if ident in related or ident not in by_id:
                continue
            item = by_id[ident]
            related[ident] = item
            for field in ('depends_on', 'on_success', 'on_failure'):
                pending.extend(link_ids(item.get(field)))
        by_id = related
    graph = {ident: [] for ident in by_id}
    for item in by_id.values():
        for target in link_ids(item.get('on_success')) + link_ids(item.get('on_failure')):
            if target not in by_id:
                raise ValueError(f'任务不存在 / Missing task: {target}')
            graph[item['id']].append(target)
        for dependency in link_ids(item.get('depends_on')):
            if dependency in by_id:
                graph[dependency].append(item['id'])
    # A completed predecessor cannot simultaneously be required to remain running.
    def dependencies(ident, seen=None):
        seen = set() if seen is None else seen
        if ident in seen or ident not in by_id:
            return set()
        seen.add(ident)
        direct = set(link_ids(by_id[ident].get('depends_on')))
        return direct | set().union(*(dependencies(i, seen) for i in direct)) if direct else set()
    for source in by_id:
        pending = link_ids(by_id[source].get('on_success')) + link_ids(by_id[source].get('on_failure'))
        seen = set()
        while pending:
            target = pending.pop()
            if target in seen:
                continue
            seen.add(target)
            if source in dependencies(target):
                raise ValueError('后续任务不能要求已完成的上游仍在运行 / Completed predecessor cannot be a running dependency')
            pending.extend(link_ids(by_id[target].get('on_success')) + link_ids(by_id[target].get('on_failure')))
    done, active = set(), set()
    def visit(ident):
        if ident in active:
            raise ValueError('任务引用形成循环 / Circular task links')
        if ident in done:
            return
        active.add(ident)
        for target in graph[ident]:
            visit(target)
        active.remove(ident)
        done.add(ident)
    for ident in graph:
        visit(ident)


def template_item(kind):
    return dict(name={'python':'Python 任务','node':'Node.js 任务','powershell':'PowerShell 任务','batch':'批处理任务'}[kind],
                path='', script_type=kind, interpreter={'python':'','node':'node','powershell':'powershell.exe','batch':''}[kind],
                args='', env_vars='PYTHONUNBUFFERED=1' if kind=='python' else '', log_output=True,
                auto_restart=False, launch_on_app_start=False, success_exit_codes=[0],on_success=[],on_failure=[])

def scan_scripts(paths, recursive=True):
    skip={'.git','.venv','venv','env','node_modules','__pycache__','.venv-build','build','dist'}
    extensions={'.py','.pyw','.js','.mjs','.cjs','.ps1','.bat','.cmd','.sh','.vbs','.wsf'}
    found={}
    def add(p):
        if p.is_file() and p.suffix.lower() in extensions:
            absolute=str(p.resolve());found[os.path.normcase(absolute)]=absolute
    for path in paths:
        p=Path(path)
        if p.is_symlink(): continue
        if p.is_file(): add(p)
        elif p.is_dir():
            for root,dirs,files in os.walk(p,followlinks=False):
                dirs[:]=[d for d in dirs if d.lower() not in skip and not Path(root,d).is_symlink()] if recursive else []
                for f in files: add(Path(root,f))
    return sorted(found.values(),key=str.casefold)

def discover_environments(script='', work_dir=''):
    results={}
    def add(exe,kind,label,root=''):
        if exe and Path(exe).is_file():
            exe=str(Path(exe).resolve());results[os.path.normcase(exe)]={'executable':exe,'kind':kind,'label':label,'root':root}
    for name,kind in [('python','python'),('python3','python'),('node','node'),('pwsh','powershell'),('powershell','powershell')]:
        add(shutil.which(name),kind,name)
    if not getattr(sys,'frozen',False): add(sys.executable,'python','当前 Python')
    bases={Path(work_dir or '.'),Path(script).parent if script else Path('.')}
    for base in bases:
        for env in (base,base/'.venv',base/'venv'):
            for sub in ('Scripts/python.exe','bin/python'):
                add(env/sub,'python',f'虚拟环境: {env}',str(env))
    conda=os.environ.get('CONDA_EXE') or shutil.which('conda.exe') or shutil.which('conda')
    if conda:
        try:
            r=subprocess.run([conda,'env','list','--json'],capture_output=True,text=True,timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            for root in json.loads(r.stdout).get('envs',[]):
                add(Path(root)/('python.exe' if os.name=='nt' else 'bin/python'),'conda',f'Conda: {Path(root).name}',root)
        except (OSError,ValueError,subprocess.TimeoutExpired): pass
    nvm=os.environ.get('NVM_HOME')
    if nvm:
        for exe in Path(nvm).glob('v*/node.exe'): add(exe,'node',f'Node {exe.parent.name}')
    for exe in (Path.home()/'.nvm/versions/node').glob('*/bin/node'): add(exe,'node',f'Node {exe.parent.parent.name}')
    return list(results.values())

def environment_vars(item):
    raw=item.get('env_vars') or ''
    if item.get('environment_kind') != 'conda': return raw
    root=item.get('environment_root') or str(Path(item.get('interpreter','')).parent)
    paths=[root,str(Path(root)/'Scripts'),str(Path(root)/'Library/bin')]
    return 'CONDA_PREFIX='+root+'\nCONDA_DEFAULT_ENV='+Path(root).name+'\nPATH='+os.pathsep.join(paths+[os.environ.get('PATH','')])+'\n'+raw

class TaskQueue:
    def __init__(self): self.pending=[]
    def submit(self,ident):
        if ident in self.pending: return False
        self.pending.append(ident);return True
    def cancel(self,ident):
        if ident in self.pending: self.pending.remove(ident)
    def take(self,limit,active,ready=lambda ident:True):
        slots=max(0,limit-len(active)) if limit else len(self.pending)
        taken=[]
        for ident in list(self.pending):
            if ident in active: continue
            if slots and ready(ident):
                self.cancel(ident);taken.append(ident);slots-=1
        return taken

def split_arguments(value):
    """Parse using the target OS argv rules, preserving quoted spaces/empty args."""
    if os.name != 'nt':
        import shlex
        return shlex.split(value)
    import ctypes
    from ctypes import wintypes
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    parse=shell.CommandLineToArgvW
    parse.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(ctypes.c_int)]
    parse.restype=ctypes.POINTER(wintypes.LPWSTR)
    count=ctypes.c_int();argv=parse('task.exe '+value,ctypes.byref(count))
    if not argv:raise ctypes.WinError(ctypes.get_last_error())
    free=ctypes.WinDLL('kernel32').LocalFree
    free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p
    try:return [argv[i] for i in range(1,count.value)]
    finally:free(ctypes.cast(argv,ctypes.c_void_p))


def merge_task_items(existing,incoming,replace=False):
    """Merge a batch and remap its internal references without stealing existing IDs."""
    incoming=migrate_items(incoming)
    if replace:
        validate_links(incoming);return incoming
    result=migrate_items(existing);used={i['id'] for i in result};mapping={};added=[]
    def key(item):
        path=item.get('path','')
        if path.startswith(('http://','https://')) or item.get('script_type') in ('command','process'):
            return (item.get('script_type'),path,item.get('http_method','GET'))
        path=Path(path)
        if not path.is_absolute():path=Path(item.get('work_dir') or '.')/path
        return os.path.normcase(str(path.resolve()))
    seen={key(i):i['id'] for i in result}
    next_id=max(used,default=0)+1
    for item in incoming:
        k=key(item)
        if k in seen:mapping[item['id']]=seen[k];continue
        new_id=item['id']
        if new_id in used:new_id=next_id
        next_id=max(next_id,new_id+1);used.add(new_id);mapping[item['id']]=new_id;seen[k]=new_id
        added.append(dict(item))
    for item in added:
        item['id']=mapping[item['id']]
        for field in ('on_success','on_failure'):
            item[field]=[mapping.get(i,i) for i in link_ids(item.get(field))]
        item['depends_on']=','.join(str(mapping.get(i,i)) for i in link_ids(item.get('depends_on')))
    result+=added;validate_links(result);return result
