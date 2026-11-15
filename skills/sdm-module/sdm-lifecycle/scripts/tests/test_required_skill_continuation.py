"""Required official Skill survives the real SDM replan boundary; control only."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from required_skills import inspect
import research_replan
import research_worker
import run_project_pipeline


class RequiredSkillContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='sdm-continuation-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        skill=self.root/'SKILL.md';skill.write_text('---\nname: lpai\ndescription: identity fixture\n---\n')
        manifest=self.root/'manifest.json';manifest.write_text(json.dumps({'name':'lpai','version':'fixture','build_sha':'fixture'}))
        def ref(path):return {'path':str(path),'sha256':'sha256:'+hashlib.sha256(path.read_bytes()).hexdigest()}
        self.required={'version':1,'platform':'lpai','required_skills':[{'name':'lpai','skill':ref(skill),'manifest':ref(manifest)}]}

    def test_actual_archived_result_child_keeps_required_skill(self):
        config={'platform_requirements':self.required,
                'research_iteration':{'version':1,'objective':'test actual result-driven continuation','targets':[]},
                'research_worker':{'project_path':str(self.root/'original-project.json')},'proposal_review':{}}
        state={'research_next':{'status':'pending-plan-review'}}
        with patch.object(research_worker,'control',return_value={'revision':3}), \
             patch.object(research_worker,'check_control'), \
             patch.object(research_replan,'advance',return_value={'status':'fixture-next-review'}) as advance:
            result=research_replan.continue_after_archive(config,self.root,state)
        self.assertEqual(result['status'],'fixture-next-review')
        child=advance.call_args.args[0]
        self.assertEqual(child['platform_requirements'],self.required)
        persisted=json.loads((self.root/'research-replan/project.json').read_bytes())
        self.assertEqual(persisted['platform_requirements'],self.required)
        self.assertEqual(inspect(child)['status'],'required-skills-available')
        self.assertNotIn('devspace_execution',child)

    def test_missing_skill_stops_replan_before_model(self):
        config={'platform_requirements':self.required,'research_replan':{'version':1,'objective':'test'}}
        (self.root/'SKILL.md').unlink()
        with patch.object(research_replan,'parent_evidence') as evidence, self.assertRaisesRegex(ValueError,'installed_skill_file'):
            research_replan.advance(config,self.root)
        evidence.assert_not_called()

    def test_missing_skill_blocks_fresh_execution_before_driver_or_review(self):
        config={'provider':'control-fixture','supported_graph_revisions':[3], 'platform_requirements':self.required}
        (self.root/'SKILL.md').unlink()
        with patch.object(run_project_pipeline,'resolve_driver') as driver, \
             patch('proposal_review.gate') as review, self.assertRaisesRegex(ValueError,'installed_skill_file'):
            run_project_pipeline.run(config,self.root/'execution')
        driver.assert_not_called();review.assert_not_called()

    def test_unfinished_study_does_not_require_new_plan_when_goal_already_met(self):
        with patch.object(research_replan,'advance') as advance:
            self.assertIsNone(research_replan.continue_after_archive({},self.root,{'research_complete':True}))
        advance.assert_not_called()


if __name__=='__main__':unittest.main()
