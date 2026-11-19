"""Read-only per-round review history; never treats one verdict as execution authority."""
from pathlib import Path


def history(data, review_root, label):
    records, issues = [], []
    if not review_root:
        return records, issues
    folders = {p.parent for name in ('accepted.json', 'request.json')
               for p in Path(review_root).glob('proposal-review/*/round-*/' + name)}
    def order(folder):
        try:
            return (folder.parent.stat().st_mtime_ns, str(folder.parent), int(folder.name[6:]))
        except (OSError, ValueError):
            return (0, str(folder.parent), 0)
    for folder in sorted(folders, key=order):
        accepted = folder / 'accepted.json'
        path = accepted if accepted.exists() else folder / 'request.json'
        try:
            raw = data.read(path)
            proposal = raw['proposal']
            pending = path != accepted
            response = {} if pending else raw['response']
            approved = not pending and raw.get('approved') is True
            disposition = ('等待本轮评审' if pending else '评审通过' if approved else
                           {'approve': '本轮认可，尚未最终通过', 'revise': '要求修改',
                            'needs-evidence': '要求补充证据', 'reject': '未通过',
                            'stalled': '评审停滞'}.get(response.get('verdict'), '已记录'))
            record = {
                'group': str(folder.parent.resolve()), 'round': int(folder.name[6:]),
                'revision': proposal.get('revision'), 'type': label,
                'pending': pending, 'approved': approved,
                'verdict': response.get('verdict'), 'disposition': disposition,
                'hypothesis': proposal.get('hypothesis'),
                'intervention': proposal.get('research_revision', {}).get('intervention'),
                'rationale': response.get('rationale'),
                'challenges': response.get('challenges', []),
                'resolutions': response.get('resolutions', []),
                'addresses': proposal.get('addresses', []),
                'advice_dispositions': proposal.get('advice_dispositions', []),
                'evidence_assessment': response.get('evidence_assessment'),
                'path': str(path),
                'evidence': data.evidence_file(path, '原始' + label + ('请求' if pending else '回执')),
            }
            records.append(record)
        except (OSError, ValueError, KeyError, TypeError):
            issues.append({'type': label, 'round': folder.name,
                           'message': '本轮记录暂不可读，请核对原始评审文件。'})
    return records, issues
