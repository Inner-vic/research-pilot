"""Verified content snapshots across WSL/Windows Git, without source/index writes.

Private indexes deliberately contain no worktree stat cache. Every invocation
re-reads tracked contents. A native result is reusable only when that exact
snapshot and environment were compared with a fresh-index baseline result.
"""
import hashlib,json,os,re,shutil,subprocess,tempfile,time
from pathlib import Path
from project_driver import file_digest
from review_storage import atomic_json
from task_pipeline import digest


class Unsupported(Exception):pass


class IndexListing(bytes):
    """Index entries plus explicitly preserved sparse skip-worktree flags."""
    pass


def command(executable,repo,args,env=None,data=None):
    result=subprocess.run([executable,'-C',str(repo),*args],input=data,capture_output=True,
                          timeout=30,env=env or {**os.environ,'GIT_OPTIONAL_LOCKS':'0'}, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:raise Unsupported('git_query_failed')
    return result.stdout


def normalized(data):
    # Match subprocess text=True universal newlines used by the original entry.
    return data.decode('utf-8').replace('\r\n','\n').replace('\r','\n').strip()


def windows(path):
    value=str(path)
    if not re.match(r'^/mnt/[a-zA-Z]/',value):raise Unsupported('not_windows_mounted_path')
    return value[5].upper()+':/'+value[7:]


def inputs(repo):
    if any(os.environ.get(k) for k in ('GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_CONFIG_COUNT','GIT_CONFIG_PARAMETERS','GIT_COMMON_DIR')):
        raise Unsupported('custom_git_environment')
    config=command('git',repo,['config','--null','--list'])
    pairs=[row.split(b'\n',1) for row in config.split(b'\0') if row]
    values={}
    for row in pairs:
        values[row[0].decode()]=row[1].decode() if len(row)==2 else 'true'
    if values.get('core.filemode','true').lower()!='false':raise Unsupported('posix_mode_semantics')
    if any(k.lower().startswith(('filter.','diff.','include.','includeif.')) for k in values):raise Unsupported('custom_git_config_semantics')
    if values.get('core.fsmonitor','false').lower() not in ('false','0',''):raise Unsupported('fsmonitor_semantics')
    if values.get('core.splitindex','false').lower() not in ('false','0',''):raise Unsupported('split_index')
    if any(k.startswith('extensions.') and k!='extensions.worktreeconfig' for k in values):raise Unsupported('custom_repository_extensions')
    if values.get('core.attributesfile'):raise Unsupported('global_attributes')
    for p in (Path.home()/'.config/git/attributes',Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'git/attributes',Path('/etc/gitattributes')):
        if p.exists():raise Unsupported('global_attributes')
    top=normalized(command('git',repo,['rev-parse','--show-toplevel']))
    if Path(top)!=repo:raise Unsupported('registered_subdirectory')
    head=normalized(command('git',repo,['rev-parse','HEAD']))
    gitdir=Path(normalized(command('git',repo,['rev-parse','--absolute-git-dir'])))
    if (gitdir/'info/attributes').exists():raise Unsupported('repository_attributes')
    listing=IndexListing(command('git',repo,['ls-files','--stage','-z']))
    tags=command('git',repo,['ls-files','-v','-z'])
    if any(row[:1] not in (b'H',b'S') for row in tags.split(b'\0') if row):raise Unsupported('index_flags')
    listing.skip_paths=tuple(row[2:] for row in tags.split(b'\0') if row.startswith(b'S '))
    flags=command('git',repo,['ls-files','--debug'])
    if any(int(value,16) not in (0,0x40000000,0x40004000) for value in re.findall(rb'flags: ([0-9a-fA-F]+)',flags)):
        raise Unsupported('index_flags')
    parents={Path(top)}
    for entry in listing.split(b'\0'):
        if not entry:continue
        meta,name=entry.split(b'\t',1);mode,oid,stage=meta.split()
        if mode not in (b'100644',b'100755') or stage!=b'0':raise Unsupported('non_regular_or_conflicted_index')
        path=Path(top)/name.decode('utf-8')
        if not path.is_relative_to(Path(top)):raise Unsupported('index_path')
        parents.update(p for p in path.parents if p==Path(top) or p.is_relative_to(Path(top)))
    if any((parent/'.gitattributes').exists() for parent in parents):raise Unsupported('repository_attributes')
    index_path=normalized(command('git',repo,['rev-parse','--git-path','index']))
    index=Path(index_path) if Path(index_path).is_absolute() else repo/index_path
    identity={'root':top,'head':head,'index_digest':file_digest(index),'config_digest':digest(config.hex()),
              'listing_digest':digest(listing.hex()),'baseline_version':normalized(command('git',repo,['--version']))}
    return identity,listing,values


def environment(values,index,native):
    env={**os.environ,'GIT_OPTIONAL_LOCKS':'0','GIT_INDEX_FILE':windows(index) if native else str(index),
         'GIT_NO_LAZY_FETCH':'1'}
    # Preserve sparse semantics and explicit skip-worktree flags. Do not turn
    # intentionally absent sparse paths into deletions.
    merged=dict(values)
    for key in list(merged):
        if key.startswith('remote.') and key.endswith('.promisor'):merged[key]='false'
    if native:
        env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='NUL',GIT_ATTR_NOSYSTEM='1')
        merged.update({'core.autocrlf':values.get('core.autocrlf','false'),'core.attributesfile':'NUL'})
    env['GIT_CONFIG_COUNT']=str(len(merged))
    for i,(key,value) in enumerate(sorted(merged.items())):
        env['GIT_CONFIG_KEY_'+str(i)]=key;env['GIT_CONFIG_VALUE_'+str(i)]=value
    if native:
        forwarded=['GIT_OPTIONAL_LOCKS','GIT_INDEX_FILE','GIT_NO_LAZY_FETCH','GIT_CONFIG_NOSYSTEM','GIT_CONFIG_GLOBAL','GIT_ATTR_NOSYSTEM','GIT_CONFIG_COUNT']
        forwarded += [name for name in env if name.startswith(('GIT_CONFIG_KEY_','GIT_CONFIG_VALUE_'))]
        env['WSLENV']=':'.join(filter(None,[os.environ.get('WSLENV',''),*forwarded]))
    return env


def fresh(executable,repo,listing,values,temporary,native,head):
    index=temporary/('native-index' if native else 'baseline-index')
    env=environment(values,index,native);cwd=windows(repo) if native else str(repo)
    reported=normalized(command(executable,cwd,['rev-parse','--git-path','index'],env))
    expected=windows(index) if native else str(index)
    if (reported.replace('\\','/').casefold() if native else reported)!=(expected.casefold() if native else expected):
        raise Unsupported('private_index_environment_not_honored')
    command(executable,cwd,['update-index','-z','--index-info'],env,listing)
    if getattr(listing,'skip_paths',()):
        command(executable,cwd,['update-index','--skip-worktree','-z','--stdin'],env,b'\0'.join(listing.skip_paths)+b'\0')
    status=normalized(command(executable,cwd,['status','--porcelain','--untracked-files=no'],env))
    diff=normalized(command(executable,cwd,['diff','--binary',head],env))
    return {'commit':head,'dirty':bool(status),'tracked_diff_digest':digest(diff),
            'status_digest':digest(status),'snapshot_mode':'fresh-index-v2'}


def accept_proof(record,fingerprint,candidate):
    return (isinstance(record,dict) and record.get('schema')=='sdm-git-equivalence/v1'
            and record.get('fingerprint')==fingerprint and record.get('candidate')==candidate
            and record.get('baseline')==candidate and record.get('proof_digest')==digest(
                {'fingerprint':fingerprint,'candidate':candidate,'baseline':candidate}))


def snapshot(repo,workspace,profile):
    """None selects the unchanged legacy path; mismatch selects safe baseline."""
    native=shutil.which('git.exe')
    if not native or not re.match(r'^/mnt/[a-zA-Z]/',str(repo)):
        profile['optimization']='legacy-runtime';return None
    started=time.perf_counter()
    try:
        identity,listing,values=inputs(repo)
        native_version=normalized(command(native,windows(repo),['--version']))
        fingerprint=digest({'identity':identity,'native_version':native_version,
                            'native_executable':file_digest(native),'implementation':file_digest(Path(__file__))})
        proof_dir=workspace/'repository-equivalence'
        proof_dir.mkdir(parents=True,exist_ok=True)
        # Temporary indexes live under this Windows-mounted workspace and are
        # removed even on failure. Never refresh or update the real Git index.
        with tempfile.TemporaryDirectory(prefix='query-',dir=proof_dir) as directory:
            temporary=Path(directory)
            tick=time.perf_counter();candidate=fresh(native,repo,listing,values,temporary,True,identity['head'])
            profile['native_seconds']=time.perf_counter()-tick
            path=proof_dir/(digest({'fingerprint':fingerprint,'candidate':candidate}).split(':')[1]+'.json')
            try:record=json.loads(path.read_bytes()) if path.exists() and path.stat().st_size<65536 else None
            except (ValueError,OSError):record=None
            if accept_proof(record,fingerprint,candidate):
                chosen=candidate;profile['optimization']='verified-native-content'
            else:
                tick=time.perf_counter();baseline=fresh('git',repo,listing,values,temporary,False,identity['head'])
                profile['equivalence_seconds']=time.perf_counter()-tick
                chosen=baseline
                if candidate==baseline:
                    body={'fingerprint':fingerprint,'candidate':candidate,'baseline':baseline}
                    atomic_json(path,{'schema':'sdm-git-equivalence/v1',**body,'proof_digest':digest(body)})
                    profile['optimization']='equivalence-established'
                else:profile['optimization']='baseline-on-mismatch'
            final,_,_=inputs(repo)
            if final!=identity:
                profile['drift_keys']=[k for k in identity if final.get(k)!=identity[k]]
                raise ValueError('repository_changed_during_snapshot')
        profile['query_seconds']=time.perf_counter()-started
        return identity['root'],{k:v for k,v in chosen.items() if k!='status_digest'}
    except (Unsupported,OSError,UnicodeError,ValueError,subprocess.SubprocessError) as error:
        if isinstance(error,ValueError) and str(error)=='repository_changed_during_snapshot':raise
        profile['optimization']='legacy-fallback'
        profile['fallback_reason']=type(error).__name__+':'+str(error)[:160]
        return None
