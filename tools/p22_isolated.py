"""Run the frozen P22 forward validation from an isolated, hash-verified source tree.

P25 changed ops/paper_experiment.py, one of the 20 P22-pinned sources, so `frozen()` now fails closed
in the working tree (P22_FROZEN_CODE_CHANGED). This tool does NOT re-register hashes, weaken or
bypass `frozen()`, or monkeypatch anything. It builds a disposable copy of the tracked tree, replaces
only the pinned files whose working copy differs with their exact registered bytes (stored under
backtesting/p22_pinned/), verifies protocol/registration/runner and all 20 pinned hashes, and then
runs the unchanged `python -m backtesting.p22_forward ...` inside that copy, where the unchanged
`frozen()` re-verifies everything itself. New PAPER input policies cannot reach P22.

Usage: python tools/p22_isolated.py {collect|analyze} [p22_forward arguments...]
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
PROTOCOL='backtesting/p22_protocol.json'
REGISTRATION='backtesting/p22_registration.json'
RUNNER='backtesting/p22_forward.py'
# Constants of the registered P22 (master 91d098f..6c2d8de, unchanged by P25). Any drift blocks the run.
PROTOCOL_SHA='4aa290d9077f0a134a376b01b8560f2eddaf65854f1bd329d6e73b9373266af4'
REGISTRATION_SHA='636ad56462e7ac5211d41ff29f9f72b103c885637ec5150c8d53b5108644a715'
RUNNER_SHA='20e892b5890cde5a84585897a6efb67570de7bef7e9bb542b1047c5453bb054a'
PINNED_COPIES={'ops/paper_experiment.py':'backtesting/p22_pinned/paper_experiment.py.pin'}
COMMANDS=('collect','analyze')


class IsolationError(ValueError):pass


def require(ok,code):
    if not ok:raise IsolationError(code)


def sha(path):return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def pinned_hashes(root=ROOT):
    require(sha(Path(root)/PROTOCOL)==PROTOCOL_SHA,'P22_PROTOCOL_CHANGED')
    return json.loads((Path(root)/PROTOCOL).read_text(encoding='utf-8'))['code_sha256']


def working_tree_drift(root=ROOT):
    """Pinned files whose working copy differs from the registration (expected: only P25's file)."""
    return sorted(name for name,digest in pinned_hashes(root).items() if sha(Path(root)/name)!=digest)


def verify(tree):
    """Exact registration of the isolated tree; raises on any mismatch."""
    tree=Path(tree)
    expected=pinned_hashes(tree)
    require(sha(tree/REGISTRATION)==REGISTRATION_SHA,'P22_REGISTRATION_UNVERIFIED')
    require(sha(tree/RUNNER)==RUNNER_SHA,'P22_RUNNER_CHANGED')
    require(len(expected)==20,'P22_PIN_SET_CHANGED')
    for name,digest in expected.items():
        require(sha(tree/name)==digest,'P22_FROZEN_CODE_CHANGED')
    return expected


def tracked(root=ROOT):
    out=subprocess.run(['git','-C',str(root),'ls-files','-z'],capture_output=True,check=False)
    require(out.returncode==0,'GIT_FILE_LIST_UNAVAILABLE')
    return [n for n in out.stdout.decode().split('\0') if n]


def build(dest,root=ROOT):
    """Copy tracked regular files, overlay exact registered bytes for drifted pins, verify."""
    root=Path(root);dest=Path(dest)
    require(not dest.exists(),'ISOLATED_TREE_EXISTS')
    drift=set(working_tree_drift(root))
    require(drift<=set(PINNED_COPIES),'P22_UNEXPECTED_PINNED_DRIFT')  # Only known, pinned replacements.
    dest.mkdir(mode=0o700)
    for name in tracked(root):
        source=root/name
        if source.is_symlink() or not source.is_file():continue
        target=dest/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    for name,pin in PINNED_COPIES.items():
        require(sha(root/pin)==pinned_hashes(root)[name],'P22_PINNED_COPY_UNVERIFIED')
        shutil.copyfile(root/pin,dest/name)
    verify(dest)
    return sorted(drift)


def run(args,root=ROOT,*,python=sys.executable,extra_env=None,module='backtesting.p22_forward'):
    require(len(args)>=1 and (module!='backtesting.p22_forward' or args[0] in COMMANDS),'P22_COMMAND_REQUIRED')
    parent=Path(tempfile.mkdtemp(prefix='p22-isolated-')).resolve()  # macOS /var -> /private/var
    try:
        tree=parent/'source';replaced=build(tree,root)
        env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','PYTHONSTARTUP')}
        env.update(PYTHON_DOTENV_DISABLED='1',P22_ISOLATED_TREE='1',**(extra_env or {}))
        result=subprocess.run([python,'-m',module,*args],cwd=tree,env=env)
        return result.returncode,replaced
    finally:
        def unlock(func,path,_):
            os.chmod(path,stat.S_IRWXU);func(path)
        shutil.rmtree(parent,onerror=unlock)


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in COMMANDS:
        print(__doc__.strip().splitlines()[-1]);return 2
    try:code,replaced=run(args)
    except IsolationError as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc),'network_calls':0}));return 2
    print(json.dumps({'p22_isolated':True,'replaced_with_registered_bytes':replaced,'exit_code':code}),file=sys.stderr)
    return code


if __name__=='__main__':raise SystemExit(main())
