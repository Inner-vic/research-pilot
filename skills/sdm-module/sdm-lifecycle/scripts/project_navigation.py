"""Build a project-wide record catalog without overwriting its README."""
import json
from pathlib import Path
from urllib.parse import quote, urlsplit


def build(repository, directory, settings):
    repo = Path(repository).resolve()
    root = repo / directory
    if not root.resolve().is_relative_to(repo) or '.git' in Path(directory).parts:
        raise ValueError('navigation_path_must_stay_inside_project')
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
    rows = []
    target = root / 'catalog.md'
    if target.is_symlink() or not target.resolve().is_relative_to(repo):
        raise ValueError('linked_navigation_not_allowed')
    for file in sorted(root.glob('*/round-*/result.json')):
        if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
            raise ValueError('linked_result_not_allowed')
        item = json.loads(file.read_bytes())
        rows.append((file, item))
    lines = ['# 项目执行进展', '', '每次运行按运行编号和轮次保留结果；持续维护同一个工作分支。', '']
    branch, url = settings.get('branch'), settings.get('remote_url')
    if branch and url:
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('credential_free_project_url_required')
        base = url[:-4] if url.endswith('.git') else url.rstrip('/')
        ref = quote(branch, safe='')
        lines += [f'当前维护分支：`{cell(branch)}`。', '',
                  f'- [当前分支]({base}/-/tree/{ref})',
                  f'- [提交记录]({base}/-/commits/{ref})', '']
    lines += ['| 运行编号 | 轮次 | 状态 | 报告 | 完整结果 | 下一步 |', '|---|---|---|---|---|---|']
    for file, item in rows:
        relative = file.parent.relative_to(root).as_posix()
        if not (file.parent / 'report.md').is_file():
            report = '报告待补齐'
        else:
            report = f'[报告]({quote(relative, safe="/")}/report.md)'
        values = [cell(item['run_id']), cell(item['round']), cell(item['status']), report,
                  f'[JSON]({quote(relative, safe="/")}/result.json)', cell((item.get('analysis') or {}).get('action', 'pending'))]
        lines.append('| ' + ' | '.join(values) + ' |')
    lines += ['', '失败、未完成和分析待处理的结果也保留。Git 提交时间不等于训练时间；迁移或补录不代表新训练。',
              '', '代码、数据、配置、运行环境、真实指标和产物位置及摘要见各轮 JSON。原始数据、完整日志、权重和身份文件不放入 Git。', '']
    return target, '\n'.join(lines).encode('utf-8')
