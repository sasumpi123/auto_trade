"""Reconstruct hash-pinned pre-fix code in a disposable non-checkout directory."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=Path(__file__).parent/'fixtures'


@contextmanager
def baseline_tree():
    spec=json.loads((FIXTURES/'p14_upbit_minutes.json').read_text())
    patch=(FIXTURES/'p14_before.patch').read_text(encoding='utf-8').encode()  # Normalize checkout CRLF.
    if hashlib.sha256(patch).hexdigest()!=spec['patch_sha256']:raise ValueError('BASELINE_PATCH_SHA_MISMATCH')
    with tempfile.TemporaryDirectory(dir='/tmp') as temp:
        source=Path(temp).resolve()/'source';source.mkdir(mode=0o700)
        # No research data, holdout, credentials, journals, .git or worktree operations.
        names=subprocess.check_output(['git','-C',str(ROOT),'ls-files','-z','--',
            'ops','services','utils','strategies','trading','data_analyzer']).decode().split('\0')
        for name in names:
            if not name or Path(name).suffix!='.py':continue
            target=source/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,target)
        shutil.copyfile(ROOT/'config.py',source/'config.py')
        (source/'.github/automation').mkdir(parents=True)
        for name in ('policy.json','zero-cost.json'):
            shutil.copyfile(ROOT/'.github/automation'/name,source/'.github/automation'/name)
        path=Path(temp)/'before.patch';path.write_bytes(patch)
        for check in (True,False):
            args=['git','apply','--reverse']+(['--check'] if check else [])+[str(path)]
            r=subprocess.run(args,cwd=source,capture_output=True,text=True)
            if r.returncode:raise ValueError('BASELINE_RECONSTRUCTION_FAILED_NOT_A_RED_TEST')
        hashes={p:hashlib.sha256((source/p).read_text(encoding='utf-8').encode()).hexdigest() for p in spec['baseline_source_sha256']}
        if hashes!=spec['baseline_source_sha256']:raise ValueError('BASELINE_SOURCE_NOT_EXACT_MASTER')
        yield source,hashes
