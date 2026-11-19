"""Read-only SDM21 source/provenance and gate-DSL audit, never a study runner.

Outputs must be outside the source tree. Requires the declared Git baseline;
an exported snapshot without that history cannot reconstruct provenance.
The synthetic contexts test policy differences, not authenticated acceptance.
"""
import argparse
import ast
import copy
import datetime
import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
BASE = 'ec870b0e12b0932741a193520a004cba5ae9f3c3'
PREFIX = 'skills/sdm-module/sdm-lifecycle/'
MODULES = '''canonical contracts workspace ledger projection authority handoffs specctl
research_initialize research_runtime research_supervisor research_event_service event_coordinator
run_project_pipeline component_pipeline component_submission component_payload component_results
component_research component_candidates component_reproduction component_execution_evidence
component_event_source component_task_inventory research_replan research_implementation
implementation_validation proposal_review proposal_model_runner lpai_manager submission_guard
task_pipeline workflow_definition exploration_workflow orchestration work_packages research_pause
research_recovery research_restore study_accounting study_successor devspace_study'''.split()
ASSETS = '''lifecycle-steps.json transition-requirements.json artifact-contracts.json artifact-catalog.json
authority-policy.json platform-operations.json supporting-artifact-catalog.json event-types.json
command-contracts.json bmad-dependencies.json'''.split()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def definitions(raw):
    result = {}
    for node in ast.parse(raw).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result[node.name + '.' + child.name] = child
    return result


def run(root, output, baseline):
    root = Path(root).resolve(); output = Path(output).resolve()
    if output.is_relative_to(root):
        raise ValueError('audit_output_must_be_outside_source')
    subprocess.run(['git', 'cat-file', '-e', baseline + '^{commit}'], cwd=root,
                   check=True, capture_output=True)
    paths = [PREFIX + 'scripts/' + name + '.py' for name in MODULES]
    paths += [PREFIX + 'assets/' + name for name in ASSETS]
    paths += ['skills/researchpilot/scripts/researchpilot.py', 'scripts/build-native-skill.py',
              'skills/researchpilot/SKILL.md', PREFIX + 'SKILL.md']
    files = {}; originals = {}
    for name in paths:
        raw = (root / name).read_bytes()
        previous = subprocess.run(['git', 'show', baseline + ':' + name], cwd=root,
                                  capture_output=True)
        prior = previous.stdout if previous.returncode == 0 else None
        originals[name] = prior
        row = {'sha256': sha(raw), 'baseline': 'absent' if prior is None else
               'identical' if raw == prior else 'modified'}
        if prior is not None:
            row['baseline_sha256'] = sha(prior)
        if name.endswith('.py'):
            old = definitions(prior) if prior is not None else {}
            row['definitions'] = {}
            for key, node in definitions(raw).items():
                before = old.get(key); dump = ast.dump(node, include_attributes=False)
                row['definitions'][key] = {
                    'line': node.lineno, 'end_line': node.end_lineno,
                    'ast_sha256': sha(dump.encode()),
                    'baseline': 'absent' if before is None else 'identical' if
                    dump == ast.dump(before, include_attributes=False) else 'modified'}
        files[name] = row
    step_path = PREFIX + 'assets/lifecycle-steps.json'
    original = json.loads(originals[step_path])['steps']
    current = json.loads((root / step_path).read_bytes())['steps']
    assert len(original) == len(current) == 21
    assert [r['step'] for r in current] == list(range(1, 22))
    steps = [{'step': a['step'], 'original': a, 'current': b,
              'changed_keys': sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))}
             for a, b in zip(original, current)]
    sys.path.insert(0, str(root / PREFIX / 'scripts'))
    contracts = importlib.import_module('contracts')
    gate_tree = ast.parse((root / PREFIX / 'scripts/component_submission.py').read_bytes())
    component_gate = ast.literal_eval(next(n.value for n in gate_tree.body if
        isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'GATE' for t in n.targets)))

    def facts(tree, hpo):
        ctx = {'checks': {}, 'platform_results': {}, 'authorities': {}, 'hpo_enabled': hpo}
        def visit(node):
            kind, value = next(iter(node.items()))
            if kind in ('all_of', 'sequence', 'any_of'):
                for child in value: visit(child)
            elif kind == 'when':
                visit(value['then'] if hpo else value.get('else', {'check': 'true'}))
            elif kind == 'check': ctx['checks'][value] = True
            elif kind in ('operation', 'platform_result'): ctx['platform_results'][value] = 'successful'
            elif kind == 'authority': ctx['authorities'][value] = True
            elif kind != 'substate_event': raise ValueError('unsupported_audit_leaf:' + kind)
        visit(tree)
        return ctx

    probes = []
    for version, rows in [('original', original), ('current', current),
                          ('component', [{'step': 'submission', 'gate': component_gate}])]:
        for row in rows:
            for hpo in ([False, True] if row['step'] in (6, 15) else [False]):
                ctx = facts(row['gate'], hpo)
                passed, missing, _ = contracts.evaluate_gate(row['gate'], ctx)
                assert passed and not missing
                probes.append({'policy': version, 'step': row['step'], 'hpo_enabled': hpo,
                               'case': 'all_selected_leaf_facts', 'accepted': passed})
                for section in ('checks', 'platform_results', 'authorities'):
                    for key in ctx[section]:
                        altered = copy.deepcopy(ctx); altered[section].pop(key)
                        passed, missing, _ = contracts.evaluate_gate(row['gate'], altered)
                        assert not passed, (version, row['step'], section, key)
                        probes.append({'policy': version, 'step': row['step'], 'hpo_enabled': hpo,
                                       'case': 'missing:' + section + ':' + key,
                                       'accepted': passed, 'missing': missing})
    component_context = facts(component_gate, False)
    component_accepted = contracts.evaluate_gate(component_gate, component_context)[0]
    comparisons = []
    for row in current:
        passed, missing, _ = contracts.evaluate_gate(row['gate'], component_context)
        comparisons.append({'sdm_step': row['step'], 'accepted': passed, 'missing': missing})
    rule = {'schema': 'sdm-scientific-rule/v1',
            'inconclusive_rule': 'underpowered_or_no_decisive_result',
            'invalid_rule': 'protocol_invalid', 'support_predicate': 'support',
            'rejection_predicate': 'reject'}
    planned = [{'run_id': 'run-one', 'required_attempt_ids': ['attempt-one']}]
    attempt = {'run_id': 'run-one', 'attempt_id': 'attempt-one',
               'terminal_status': 'successful', 'accounted': True,
               'validity': 'valid', 'system_failure': False}
    evidence = {'protocol_invalid': False, 'underpowered': False,
                'predicates': {'support': True, 'reject': False}}
    scientific = []
    for label, changes, expected in [
            ('support', {}, 'hypothesis_supported'),
            ('reject', {'predicates': {'support': False, 'reject': True}}, 'hypothesis_rejected'),
            ('no-decisive-fact', {'predicates': {'support': False, 'reject': False}}, 'inconclusive'),
            ('underpowered-before-support', {'underpowered': True}, 'inconclusive'),
            ('invalid-before-underpowered', {'protocol_invalid': True, 'underpowered': True}, 'invalid')]:
        result = contracts.evaluate_scientific_decision(rule, planned, [attempt], {**evidence, **changes})
        assert result['outcome'] == expected
        scientific.append({'case': label, 'outcome': result['outcome']})
    for label, attempts, altered_evidence, expected in [
            ('missing-attempt', [], evidence, 'scientific_accounting_incomplete'),
            ('duplicate-attempt', [attempt, attempt], evidence, 'scientific_accounting_incomplete'),
            ('wrong-run', [{**attempt, 'run_id': 'other-run'}], evidence, 'scientific_accounting_incomplete'),
            ('non-terminal', [{**attempt, 'terminal_status': 'running'}], evidence, 'scientific_accounting_incomplete'),
            ('contradictory-predicates', [attempt], {**evidence, 'predicates': {'support': True, 'reject': True}},
             'scientific_rule_not_mutually_exclusive')]:
        try:
            contracts.evaluate_scientific_decision(rule, planned, attempts, altered_evidence)
        except contracts.ContractError as exc:
            assert exc.code == expected, (label, exc.code)
            scientific.append({'case': label, 'rejected_with': exc.code})
        else:
            raise AssertionError('scientific_negative_accepted:' + label)
    host = {}
    for label, action in [('AssetBundle.load', lambda: contracts.AssetBundle.load()),
                          ('ledger.import', lambda: importlib.import_module('ledger'))]:
        try:
            action(); host[label] = {'status': 'available'}
        except Exception as exc:
            host[label] = {'status': 'blocked', 'exception': type(exc).__name__,
                           'code': getattr(exc, 'code', None),
                           'missing_module': getattr(exc, 'name', None),
                           'cause': type(exc.__cause__).__name__ if exc.__cause__ else None}
    report = {
        'schema': 'sdm21-evolution-audit/v1',
        'at': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
        'baseline': baseline, 'source_version': (root / 'VERSION').read_text().strip(),
        'files': files, 'steps': steps, 'component_submission_gate': component_gate,
        'gate_dsl_probes': {'count': len(probes), 'all_expectations_passed': True, 'cases': probes},
        'scientific_decision_probes': {'count': len(scientific), 'all_expectations_passed': True,
                                       'cases': scientific,
                                       'scope': 'Actual retained pure decision function with synthetic governed facts; not full D05 acceptance.'},
        'synthetic_non_equivalence': {'component_gate_accepted': component_accepted,
                                     'current_lifecycle_gates_with_same_facts': comparisons},
        'host_probes': host,
        'limitations': [
            'Baseline is the locally retained original template commit, not a verified latest upstream release.',
            'AST equality proves syntactic reuse, not transitive semantic equality.',
            'Synthetic gate contexts test the DSL and predicate differences only. They do not replay authenticated artifacts, signatures, platforms or complete lifecycle acceptance.',
            'No ML, model, network, platform, research control or workspace mutation.'],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'files': len(files), 'steps': len(steps), 'gate_dsl_probes': len(probes),
                      'scientific_decision_probes': len(scientific),
                      'host': host, 'output': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--baseline', default=BASE)
    args = parser.parse_args()
    run(args.source, args.output, args.baseline)
