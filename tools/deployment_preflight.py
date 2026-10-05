"""Read-only deployment evidence gate. This tool never deploys or sends orders."""
import argparse
import json
import subprocess
from pathlib import Path

REQUIRED = ('runtime_source', 'candidate_integrity', 'required_files', 'tests', 'account_exposure')

def evaluate(evidence, head, clean):
    reasons = []
    if not clean:
        reasons.append('DIRTY_WORKTREE')
    if evidence.get('commit') != head:
        reasons.append('EVIDENCE_COMMIT_MISMATCH')
    for key in REQUIRED:
        if evidence.get(key) != 'VERIFIED':
            reasons.append('UNVERIFIED_' + key.upper())
    if evidence.get('deploy_authorized') is not True:
        reasons.append('DEPLOY_NOT_AUTHORIZED')
    return {'DEPLOYMENT_PREFLIGHT_GATE': 'BLOCK' if reasons else 'PASS',
            'BLOCK_REASONS': reasons}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        evidence = json.loads(args.evidence.read_text())
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        clean = not subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip()
        result = evaluate(evidence, head, clean)
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError) as exc:
        result = {'DEPLOYMENT_PREFLIGHT_GATE': 'BLOCK', 'BLOCK_REASONS': [type(exc).__name__]}
    print(json.dumps(result, indent=2))
    return 0 if result['DEPLOYMENT_PREFLIGHT_GATE'] == 'PASS' else 1

if __name__ == '__main__':
    raise SystemExit(main())
