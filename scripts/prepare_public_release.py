"""Prepare same-run public files locally. No GitHub client or token access."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import sys

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.publish_prerelease import (COMMIT_RE, REPOSITORY, RunnerContext,
                                        prepare_release, require)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY, 'Unexpected repository')
    commit = os.environ.get('GITHUB_SHA', '')
    require(COMMIT_RE.fullmatch(commit) is not None, 'Invalid source commit')
    run_id, attempt = (os.environ.get(n, '') for n in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'))
    require(all(v.isascii() and v.isdecimal() and 0 < len(v) <= 20 and int(v) > 0
                for v in (run_id, attempt)), 'Invalid workflow identity')
    prepared = prepare_release(args.artifact_root, args.artifact_root / 'artifacts/verified-bundle.json',
                    args.output_dir, RunnerContext(commit, run_id, attempt))
    from scripts.public_release_receipt import write_receipt
    write_receipt(prepared)
    print('Public release files prepared; no release published.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
