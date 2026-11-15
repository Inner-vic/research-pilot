"""Bounded resumable public-data download; no credentials, model or evaluation."""


def fetch(url,target,expected,algorithm='sha256',max_bytes=32*1024*1024,wall_seconds=180,progress=False):
    import hashlib,json,re,time
    from pathlib import Path
    from urllib.request import Request,urlopen
    if (not url.startswith('https://') or algorithm not in ('sha256','md5')
            or not re.fullmatch('[0-9a-f]{64}' if algorithm=='sha256' else '[0-9a-f]{32}',expected)):
        raise ValueError('pinned_public_https_source_required')
    target=Path(target);partial=target.with_suffix(target.suffix+'.part');binding=partial.with_suffix(partial.suffix+'.json')
    identity={'url':url,'algorithm':algorithm,'expected':expected,'max_bytes':max_bytes}
    if target.exists():
        if hashlib.new(algorithm,target.read_bytes()).hexdigest()!=expected:raise ValueError('cached_public_source_identity_changed')
        return target.read_bytes()
    target.parent.mkdir(parents=True,exist_ok=True)
    if binding.exists():
        saved=json.loads(binding.read_bytes())
        if saved['identity']!=identity:raise ValueError('partial_public_source_identity_changed')
    elif partial.exists():raise ValueError('unbound_partial_source_reconcile_required')
    started=time.monotonic()
    def record(status):
        value={'identity':identity,'bytes':partial.stat().st_size if partial.exists() else target.stat().st_size if target.exists() else 0,
            'status':status,'model_executed':False,'updated_at':time.time()}
        temporary=binding.with_suffix('.tmp');temporary.write_text(json.dumps(value),encoding='utf-8');temporary.replace(binding)
        if progress:print('SDM_JSON '+json.dumps({'kind':'public-source-progress','phase':status,'bytes':value['bytes'],'fit_started':False}),flush=True)
    try:
        while time.monotonic()-started<wall_seconds:
            offset=partial.stat().st_size if partial.exists() else 0;before=offset
            headers={'User-Agent':'SDM public dataset preparation/1.0'}
            if offset:headers['Range']='bytes='+str(offset)+'-'
            record('requesting');total_size=None
            try:
                with urlopen(Request(url,headers=headers),timeout=20) as response:
                    if response.status==206:
                        match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                        if not match or int(match[1])!=offset:raise ValueError('public_source_range_changed')
                        total_size=int(match[3]);mode='ab'
                    elif response.status==200:
                        mode='wb';offset=0
                        if response.headers.get('Content-Length'):total_size=int(response.headers['Content-Length'])
                    else:raise ValueError('successful_public_source_response_required')
                    if total_size and total_size>max_bytes:raise ValueError('public_source_size_limit')
                    recorded=offset;recorded_at=time.monotonic()
                    with partial.open(mode) as stream:
                        while True:
                            if time.monotonic()-started>wall_seconds:raise TimeoutError('public_source_total_deadline')
                            block=response.read1(64*1024)
                            if not block:break
                            offset+=len(block)
                            if offset>max_bytes:raise ValueError('public_source_size_limit')
                            stream.write(block);stream.flush()
                            if offset-recorded>=256*1024 or time.monotonic()-recorded_at>=5:
                                record('downloading');recorded=offset;recorded_at=time.monotonic()
                raw=partial.read_bytes()
                if hashlib.new(algorithm,raw).hexdigest()==expected:
                    partial.replace(target);record('verified');return raw
                if total_size is not None and offset<total_size and offset>before:
                    record('truncated-response-resume');continue
                raise ValueError('downloaded_public_source_identity_mismatch')
            except OSError:
                if (partial.exists() and partial.stat().st_size>before
                        and time.monotonic()-started<wall_seconds):
                    record('interrupted-response-resume');continue
                raise
        raise TimeoutError('public_source_total_deadline')
    except (OSError,ValueError):
        record('incomplete-reconcile-same-source');raise
