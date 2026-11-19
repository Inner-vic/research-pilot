"""Runtime progression is observable without granting execution authority."""
import copy
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research_progress import runtime_view as project


class ProgressTests(unittest.TestCase):
 def state(self,stage='watching',**fields):
  return {'status':'running','attempts':[{'stage':stage,**fields}]}

 def test_live_controller_with_blocked_review_is_not_active_iteration(self):
  state=self.state('needs-attention',reason='original-initial-review:awaiting-proposal-evidence',
   continuation={'phase':'blocked'});before=copy.deepcopy(state)
  view=project(state,owner_alive=True,worker_ready=True)
  self.assertTrue(view['controller_running']);self.assertFalse(view['automatic_iteration_active'])
  self.assertEqual(view['state'],'blocked');self.assertEqual(view['label'],'等待评审证据')
  self.assertEqual(state,before);self.assertFalse(view['execution_authority'])

 def test_watching_is_active_but_distinct_from_model_work(self):
  view=project(self.state(),owner_alive=True,worker_ready=True)
  self.assertEqual(view['state'],'waiting-training');self.assertTrue(view['automatic_iteration_active'])
  self.assertEqual(view['next_owner'],'原任务监控器')

 def test_result_handoff_distinguishes_received_and_dispatched(self):
  state=self.state('result-ready',result={'path':'record'},result_handoff={'model_requested':False})
  received=project(state,owner_alive=True,worker_ready=True)
  self.assertTrue(received['handoff']['awaiting_analysis_dispatch'])
  state['attempts'][-1].update(stage='reviewing-plan',result_handoff={'model_requested':True,'job_id':'original-job'})
  dispatched=project(state,owner_alive=True,worker_ready=True)
  self.assertFalse(dispatched['handoff']['awaiting_analysis_dispatch'])
  self.assertEqual(dispatched['handoff']['analysis_job_id'],'original-job')

 def test_retry_and_original_reconciliation_remain_explicit_wait_states(self):
  for phase in ('retry-wait','reconciling'):
   view=project(self.state(continuation={'phase':phase}),owner_alive=True,worker_ready=True)
   self.assertEqual(view['state'],phase);self.assertTrue(view['automatic_iteration_active'])
   self.assertEqual(view['model_calls'],0);self.assertEqual(view['platform_writes'],0)

 def test_dead_unverified_paused_and_completed_never_claim_active_iteration(self):
  for alive,ready,paused,kind in [(False,False,False,'offline'),(True,False,False,'unverified'),
                                (None,False,False,'unverified'),(True,True,True,'paused')]:
   view=project(self.state(),owner_alive=alive,worker_ready=ready,paused=paused)
   self.assertEqual(view['state'],kind);self.assertFalse(view['automatic_iteration_active'])
  state=self.state();state['status']='research-stopped'
  self.assertEqual(project(state,owner_alive=True,worker_ready=True)['state'],'completed')


if __name__=='__main__':unittest.main()
