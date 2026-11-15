"""Real review entrypoint routing over synthetic transport, no model/platform cost."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_roles import DEFAULT_MODELS,policy,review_settings,describe
from proposal_dialogue import prompt
from test_proposal_model_runner import ProposalRunnerTests


class RolePolicyTests(unittest.TestCase):
    def test_legacy_policy_and_prompt_bytes_preserved(self):
        cfg={'proposal_review':{'models':{'a':'old-a','b':'old-b'},'dialogue_version':2}}
        self.assertIsNone(policy(cfg));self.assertIs(review_settings(cfg,'proposal_review'),cfg['proposal_review'])
        self.assertNotIn('Planner–Executor',prompt('a',2))
        self.assertIn('Planner–Executor',prompt('a',3));self.assertIn('Critic',prompt('b',3))

    def test_explicit_conflicts_rejected_without_mutation_or_fallback(self):
        cfg={'agent_roles':{'version':1,'models':copy.deepcopy(DEFAULT_MODELS)},
             'proposal_review':{'models':{'a':'other','b':'legacy'}}}
        before=copy.deepcopy(cfg)
        with self.assertRaisesRegex(ValueError,'conflict'):review_settings(cfg,'proposal_review')
        self.assertEqual(cfg,before)
        cfg['agent_roles']['models']['critic']=cfg['agent_roles']['models']['generator']
        with self.assertRaisesRegex(ValueError,'distinct'):policy(cfg)

    def test_real_proposal_gate_routes_generator_critic_and_replays(self):
        fixture=ProposalRunnerTests(methodName='runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.cfg['agent_roles']={'version':1,'models':copy.deepcopy(DEFAULT_MODELS)}
        del fixture.cfg['proposal_review']['models'];fixture.context()
        before=copy.deepcopy(fixture.cfg)
        result=fixture.gate();self.assertEqual(result['status'],'proposal-approved')
        self.assertEqual([(r[0],r[3]) for r in fixture.calls],[('a','gpt-6-astra'),('b','gpt-6.1-sol')])
        values=[json.loads(p.read_text())['request'] for p in fixture.root.glob('proposal-review/*/model-calls/*/request.json')]
        self.assertEqual(len(values),2)
        self.assertTrue(all('Planner–Executor' in v['prompt'] for v in values))
        self.assertEqual(fixture.gate()['receipt'],result['receipt'])
        self.assertEqual(len(fixture.calls),2);self.assertEqual(fixture.cfg,before)
        self.assertFalse(describe(fixture.cfg)['execution_authority'])

    def test_result_worker_resolves_same_model_policy(self):
        cfg={'agent_roles':{'version':1,'models':copy.deepcopy(DEFAULT_MODELS)},'research_worker':{'version':5}}
        value=review_settings(cfg,'research_worker')
        self.assertEqual(value['models'],{'a':'gpt-6-astra','b':'gpt-6.1-sol'})
        self.assertEqual(value['dialogue_version'],3)
