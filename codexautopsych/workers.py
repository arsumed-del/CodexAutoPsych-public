"""Deterministic role scaffolding. None of these handlers is a live AI agent."""
import json
from .runner import execute, verify_artifacts


def handle(lab, context, owner):
    kind = context['kind']
    payload = context['payload']
    if kind == 'plan':
        exp = lab.experiment(payload['experiment'])
        return {'worker_type': context['worker_type'], 'author': owner,
                'experiment': exp['id'], 'protocol_hash': exp['protocol_hash'],
                'plan': 'Use the registered synthetic fixture runner; do not change the protocol.'}
    if kind == 'execute':
        return execute(lab, payload['experiment'], owner)
    if kind == 'audit':
        exp = lab.experiment(payload['experiment'])
        plans = [d for d in context['dependencies'] if d['role'] == 'coding-worker']
        executions = [d for d in context['dependencies'] if d['role'] == 'coordinator']
        if not plans or not executions:
            raise ValueError('Audit requires completed plan and execution dependencies')
        if any(d['owner'] == owner for d in context['dependencies']) or exp['owner'] == owner:
            raise ValueError('Reviewer must be distinct from the author and execution owner')
        if any(json.loads(d['result']).get('experiment') != exp['id'] for d in plans + executions):
            raise ValueError('Audit dependencies must refer to the same experiment')
        if exp['status'] != 'completed' or not exp['verified']:
            raise ValueError('Cannot audit unverified execution')
        manifest = verify_artifacts(lab, exp)
        return {'worker_type': context['worker_type'], 'reviewer': owner, 'experiment': exp['id'],
                'audit_passed': True, 'raw_hash': manifest['raw_hash'],
                'limitation': 'Independent artifact review is not empirical replication or AI agreement.'}
    if kind == 'report':
        audits = [d for d in context['dependencies'] if d['role'] == 'independent-reviewer']
        if not audits or not all(json.loads(d['result']).get('audit_passed') for d in audits):
            raise ValueError('Report requires successful independent audit')
        return {'worker_type': context['worker_type'], 'report': 'Synthetic workflow audited; Study 3 remains unconnected.'}
    raise ValueError('Unsupported task kind')
