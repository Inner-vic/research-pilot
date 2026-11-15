import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from model_review_bridge import prepare, accept, plan
from task_pipeline import digest


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = {'schema': 'sdm-loop-analysis-request/v1', 'remaining_rounds': 2,
            'allowed_variables': {'amp': {'type': 'boolean'}}, 'evidence': {'loss': 1.0},
            'history': [{'parameters': {'amp': True}, 'fixed': {'source_commit': 'test'},
                'training': {'actual': {'exit_code': 0, 'iteration_evidence_complete': True}},
                'evaluation': {'status': 'completed'}}]}
        cls.decision = {'request_digest': digest(cls.packet),
            'facts': [{'evidence_id': 'loss', 'value': 1.0}],
            'hypotheses': [{'statement': 'test', 'limitations': 'small sample', 'evidence_ids': ['loss']}],
            'previous_experiment_assessment': 'baseline', 'reason': 'test',
            'action': 'run-next-round', 'changed_variable': 'amp',
            'next_parameters': {'amp': False}, 'hypothesis': 'test', 'expected_observations': ['test']}

    def test_valid_response_and_tampering(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            request = Path(directory, 'request.json')
            response = Path(directory, 'response.json')
            request.write_text(json.dumps(self.packet))
            response.write_text(json.dumps(self.decision))
            prepare(request, [], directory)
            packet, decision = accept(directory, response)
            self.assertEqual(decision['action'], 'run-next-round')
            Path(directory, 'analysis-request.txt').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'evidence_integrity_failed'):
                accept(directory, response)

    def test_exhausted_history_repeated_parameters_and_no_authority(self):
        packet, decision = self.packet, self.decision
        history = packet['history'] + [{'parameters': decision['next_parameters']}]
        result = plan(packet, decision, {'history': history, 'max_rounds': 2})
        self.assertIn('current_budget_exhausted', result['blockers'])
        self.assertIn('configuration_already_executed', result['blockers'])
        self.assertIn('execution_not_authorized', result['blockers'])
        self.assertFalse(result['training_dispatched'])

    def test_stale_check_blocks_ready_context(self):
        packet, decision = self.packet, self.decision
        now = dt.datetime.now(dt.timezone.utc)
        context = {'history': packet['history'], 'max_rounds': 3,
            'execution_authorized': True, 'provider': 'test',
            'fixed_conditions': packet['history'][-1]['fixed'], 'checks': {}}
        for key in ('source', 'dataset', 'checkpoint', 'parameters', 'resource',
                    'guest_processes', 'gpu', 'provider', 'budget'):
            context['checks'][key] = {'status': 'pass', 'observed_at': now.isoformat(),
                                      'evidence_ref': 'test-receipt'}
        self.assertEqual(plan(packet, decision, context, now)['blockers'], [])
        context['checks']['gpu']['observed_at'] = (now - dt.timedelta(minutes=6)).isoformat()
        self.assertIn('preflight_missing_failed_or_stale:gpu', plan(packet, decision, context, now)['blockers'])


if __name__ == '__main__':
    unittest.main()
