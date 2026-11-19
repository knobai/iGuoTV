"""Resolve a timestamp tag's event commit without executing code from the tag."""

import os
import re
import subprocess
from datetime import datetime


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def verify(ref, event_sha):
    match = re.fullmatch(r'refs/tags/v([0-9]{10})', ref)
    if not match:
        raise SystemExit('Expected a tag in vYYMMDDHHMM format.')
    try:
        stamp = '20' + match[1]
        if datetime.strptime(stamp, '%Y%m%d%H%M').strftime('%Y%m%d%H%M') != stamp:
            raise ValueError
    except ValueError:
        raise SystemExit('Tag does not contain a valid date and minute.') from None
    if not re.fullmatch(r'[0-9a-f]{40}', event_sha):
        raise SystemExit('Expected a full GitHub event SHA.')
    commit = git('rev-parse', '--verify', ref + '^{commit}')
    if git('rev-parse', '--verify', event_sha + '^{commit}') != commit:
        raise SystemExit('The remote tag no longer matches the push event.')
    if subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'HEAD']).returncode:
        raise SystemExit('The tagged commit is not in the checked-out main history.')
    return commit


if __name__ == '__main__':
    commit = verify(os.environ['GITHUB_REF'], os.environ['GITHUB_SHA'])
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        print(f'commit={commit}', file=output)
    print(f'Verified main commit {commit}.')
