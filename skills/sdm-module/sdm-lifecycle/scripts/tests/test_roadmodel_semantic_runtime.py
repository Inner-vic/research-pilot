"""Real CPU tensor/gradient and storage-fault contracts for the new GPU pilot."""
import errno
import importlib.util
from pathlib import Path
import shutil
import os
import types
import sys
import tempfile
import unittest
from unittest.mock import patch
import torch
import torch._dynamo  # Load native operator registration before module-fixture scopes.

SCRIPTS = Path(__file__).resolve().parents[1]
ASSETS = SCRIPTS.parent/'assets/roadmodel'
sys.path[:0] = [str(SCRIPTS), str(ASSETS)]
from feature_semantic_residual import FeatureSemanticResidual
import roadmodel_runtime_safety as safety
from semantic_overlay import apply


class SemanticRuntime(unittest.TestCase):
    def inputs(self):
        torch.manual_seed(73)
        b,n,q,c,e=1,5,6,4,8
        return [torch.randn(b,q,2), torch.randn(b,n,3), torch.rand(b,n,3)+.1,
                torch.tensor([1.,0,0,0]).expand(b,n,4), torch.rand(b,n,1),
                torch.rand(b,n,c).softmax(-1), torch.randn(b,n,e,requires_grad=True),
                torch.cat([torch.rand(b,q,c-1).softmax(-1),torch.zeros(b,q,1)],-1).requires_grad_(), torch.rand(b,q)]

    def test_zero_projection_base_and_real_updates(self):
        data=self.inputs();rng=torch.get_rng_state();m=FeatureSemanticResidual(8,4,16,query_chunk=2)
        self.assertTrue(torch.equal(rng,torch.get_rng_state()))
        actual=m(*data)
        expected=m.base_probabilities(data[-2])
        torch.testing.assert_close(actual[...,:-1],expected)
        self.assertTrue(torch.equal(actual[...,-1],torch.zeros_like(actual[...,-1])))
        optimizer=torch.optim.AdamW(m.parameters(),lr=.01)
        for step in range(2):
            optimizer.zero_grad();data[6].grad=None;data[7].grad=None
            (-m(*data)[...,0].log().mean()).backward()
            self.assertGreater(data[7].grad.abs().sum().item(),0)  # base not detached
            self.assertGreater(m.readout[-1].weight.grad.abs().sum().item(),0)
            if step:self.assertGreater(data[6].grad.abs().sum().item(),0)
            optimizer.step()
        self.assertTrue(safety.finite_tree(torch,[m.state_dict(),optimizer.state_dict()]))

    def test_zero_mass_and_reject_invalid(self):
        data=self.inputs();m=FeatureSemanticResidual(8,4,16)
        data[7]=torch.zeros_like(data[7]);out=m(*data)
        torch.testing.assert_close(out[...,:3],torch.full_like(out[...,:3],1/3))
        for value in (-1.,float('nan')):
            data[7][0,0,0]=value
            with self.assertRaises(FloatingPointError):m(*data)
        data=self.inputs();data[6]=data[6][:,:-1]
        with self.assertRaises(ValueError):m(*data)

    def test_mixed_zero_and_positive_queries_keep_positive_parity_guard(self):
        data=self.inputs();data[7]=data[7].detach();data[7][0,0]=0
        m=FeatureSemanticResidual(8,4,16);m(*data)
        self.assertEqual(m.last_diagnostics['initial_zero_mass_queries'],1)
        self.assertEqual(m.last_diagnostics['initial_support_flips'],0)
        m=FeatureSemanticResidual(8,4,16)
        with torch.no_grad():m.readout[-1].bias[0]=1
        with self.assertRaisesRegex(ValueError,'positive_mass_parity'):m(*data)

    def test_support_boundary_flip_is_rejected_below_probability_tolerance(self):
        data=self.inputs();data[7]=torch.tensor([[[0.,0,0,0],[.6,.4,0,0]]]);data[0]=data[0][:,:2];data[8]=torch.tensor([[.625,.625]])
        m=FeatureSemanticResidual(8,4,16)
        with torch.no_grad():m.readout[-1].bias[0]=1e-6
        with self.assertRaisesRegex(ValueError,'positive_mass_parity'):m(*data)

    def test_checkpoint_chunks_match_outputs_and_gradients(self):
        a=FeatureSemanticResidual(8,4,16,query_chunk=2,checkpoint_chunks=True)
        b=FeatureSemanticResidual(8,4,16,query_chunk=2,checkpoint_chunks=False)
        # This comparison exercises recomputation after an established update.
        a.last_diagnostics=b.last_diagnostics={'post_initialization_comparison':True}
        with torch.no_grad():a.readout[-1].weight.normal_(0,.01)
        b.load_state_dict(a.state_dict())
        da=self.inputs();db=[x.detach().clone().requires_grad_(x.requires_grad) for x in da]
        oa=a(*da);ob=b(*db);torch.testing.assert_close(oa,ob)
        oa[...,0].sum().backward();ob[...,0].sum().backward()
        torch.testing.assert_close(da[6].grad,db[6].grad)

    def test_actual_aggregator_callback_precedes_all_support_gates(self):
        original=Path(__file__).resolve().parents[8]/'roadmodel-source-pinned'
        # Test from either the canonical module or a frozen runtime.
        if not original.is_dir():
            original=Path.cwd()/'roadmodel-source-pinned'
        self.assertTrue(original.is_dir(),'real pinned source required')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'model/head/gaussian_head';folder.mkdir(parents=True)
            for name in ('gaussian_head.py','continuous_surface_aggregate.py'):
                shutil.copyfile(original/'model/head/gaussian_head'/name,folder/name)
            manifest=apply(root);self.assertEqual(len(manifest['patched']),3)
            spec=importlib.util.spec_from_file_location('actual_aggregate',folder/'continuous_surface_aggregate.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            m=module.ContinuousGaussianSurfaceAggregator(neighbor_backend='gaussian_first')
            kwargs=dict(query_xy=torch.tensor([[[0.,0.],[.1,0.]]]),means=torch.tensor([[[.08,0.,.2]]]),
                scales=torch.tensor([[[.1,.1,.03]]]),rotations=torch.tensor([[[1.,0.,0.,0.]]]),
                opacities=torch.ones(1,1,1),semantics=torch.tensor([[[1/3,1/3,1/3,0.]]]),
                target_z=torch.zeros(1,2),lattice_shape=(2,1),lattice_origin=(0.,0.),lattice_spacing=(.1,.1))
            before=m(**kwargs)
            def callback(q,means,opa,sem,p,o):
                out=torch.zeros_like(p);out[...,0]=1;return out
            m._semantic_residual_callback=callback;after=m(**kwargs)
            torch.testing.assert_close(before['bin_logits'],after['bin_logits'])
            self.assertFalse(before['valid'][0,0].item());self.assertTrue(after['valid'][0,0].item())
            self.assertEqual(before['height'][0,0].item(),-1.)
            self.assertAlmostEqual(after['height'][0,0].item(),.2,places=5)
            self.assertEqual(before['cluster_loss'][0,0].item(),0.)
            self.assertGreater(after['cluster_loss'][0,0].item(),0.)
            self.assertNotEqual(before['target_log_prob'][0,0].item(),after['target_log_prob'][0,0].item())
            m._semantic_residual_callback=None
            for key,value in before.items():torch.testing.assert_close(value,m(**kwargs)[key])

    def test_bounded_shared_writes_and_enospc_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);clock=[0.]
            p=safety.HealthPersistence(root/'shared.json',root/'local.json',clock=lambda:clock[0])
            with patch.object(safety,'atomic',wraps=safety.atomic) as atomic:
                for i in range(100):clock[0]=i/10;p.publish({'updates':i})
                self.assertEqual(sum(c.args[0]==p.shared for c in atomic.call_args_list),1)
            real=safety.atomic
            for number in (errno.ENOSPC,errno.EDQUOT):
                def failure(path,*args,**kwargs):
                    if Path(path)==p.shared:raise OSError(number,'injected disk/quota full')
                    return real(path,*args,**kwargs)
                with patch.object(safety,'atomic',side_effect=failure):
                    with self.assertRaises(safety.StorageProtectionError):p.publish({'updates':100},force=True)
                self.assertTrue((root/'local-storage-failure.json').exists())

    def test_verified_retention_never_evicts_on_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for epoch in range(1,4):
                value=dict(state_dict={'w':torch.tensor(1.)},optimizer={},scheduler={},epoch=epoch,global_iter=epoch)
                safety.verified_checkpoint_save(torch,torch.save,value,root/f'epoch_{epoch}.pth')
            self.assertFalse((root/'epoch_1.pth').exists());self.assertTrue((root/'epoch_2.pth').exists())
            def corrupt(value,path,*a,**k):Path(path).write_bytes(b'not a checkpoint')
            with self.assertRaises(Exception):safety.verified_checkpoint_save(torch,corrupt,value,root/'epoch_4.pth')
            self.assertTrue((root/'epoch_2.pth').exists());self.assertTrue((root/'epoch_3.pth').exists())
            self.assertFalse((root/'epoch_4.pth').exists())

    def test_original_training_failure_survives_terminal_disk_failure(self):
        import training_entry_v2 as entry
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'train.py';target.write_text("raise ValueError('primary-training-error')")
            misc=types.ModuleType('misc');misc.__path__=[]
            cleaner=types.ModuleType('misc.ckpt_clean');cleaner.start_cleaner_thread=lambda:None
            def publish(self,row,force=False):
                if 'original_error' in row:raise safety.StorageProtectionError('terminal-disk-full')
            with patch.dict(sys.modules,{'misc':misc,'misc.ckpt_clean':cleaner}),patch.dict(os.environ,{'SDM_TELEMETRY_ROOT':str(root)}),patch.object(safety.HealthPersistence,'publish',publish):
                with self.assertRaisesRegex(ValueError,'primary-training-error'):entry.run(target,[])

    def test_outer_training_exit_survives_resource_sample_write_failure(self):
        class Sampler:
            def start(self):pass
            def finish(self):return {'samples':[]}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def fail(value):raise OSError(errno.ENOSPC,'injected telemetry disk full')
            with self.assertRaises(safety.TrainingProcessError) as caught:
                safety.guarded_training([sys.executable,'-c','raise SystemExit(7)'],root,os.environ.copy(),
                    root/'train.log',root/'progress.json',Sampler(),fail,maximum_seconds=5,stall_seconds=3)
            self.assertEqual(caught.exception.exit_code,7)

    def test_no_progress_child_is_reaped(self):
        class Sampler:
            def start(self):pass
            def finish(self):return {}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with self.assertRaisesRegex(safety.TrainingProcessError,'no_progress'):
                safety.guarded_training([sys.executable,'-c','import time;time.sleep(30)'],root,os.environ.copy(),
                    root/'train.log',root/'progress.json',Sampler(),lambda value:None,
                    maximum_seconds=5,stall_seconds=.3)

    def test_actual_load_optimizer_update_and_verified_save_in_entry(self):
        import training_entry_v2 as entry
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'train.py';baseline=root/'initial.pth'
            parameters={name:torch.ones(*shape) for name,shape in (
                ('head.weight',(1,1)),('head.bias',(1,)),('encoder.weight',(1,1)),('encoder.bias',(1,)))}
            torch.save(parameters,baseline)
            target.write_text("import torch,sys\nfrom pathlib import Path\n"
                "class Model(torch.nn.Module):\n def __init__(self):\n  super().__init__();self.head=torch.nn.Linear(1,1);self.encoder=torch.nn.Linear(1,1)\n"
                "m=Model();m.load_state_dict(torch.load(sys.argv[2],weights_only=True));o=torch.optim.AdamW(m.parameters())\n"
                "m.head(m.encoder(torch.ones(1,1))).sum().backward();o.step()\n"
                "torch.save(dict(state_dict=m.state_dict(),optimizer=o.state_dict(),scheduler={},epoch=1,global_iter=1),Path(sys.argv[2]).parent/'epoch_1.pth')\n")
            misc=types.ModuleType('misc');misc.__path__=[]
            cleaner=types.ModuleType('misc.ckpt_clean');cleaner.start_cleaner_thread=lambda:None
            with patch.dict(sys.modules,{'misc':misc,'misc.ckpt_clean':cleaner}),patch.dict(os.environ,{'SDM_TELEMETRY_ROOT':str(root)}):
                entry.run(target,['--load-from',str(baseline)])
            import json
            self.assertTrue(json.loads((root/'load-and-optimizer.json').read_bytes())['optimizer_verified'])
            self.assertEqual(json.loads((root/'progress.json').read_bytes())['optimizer_updates'],1)
            self.assertTrue((root/'epoch_1.pth.verified.json').exists())

    def test_reading_common_file_but_applying_other_weights_is_rejected(self):
        import training_entry_v2 as entry
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'train.py';baseline=root/'initial.pth'
            parameters={name:torch.ones(*shape) for name,shape in (
                ('head.weight',(1,1)),('head.bias',(1,)),('encoder.weight',(1,1)),('encoder.bias',(1,)))}
            torch.save(parameters,baseline)
            target.write_text("import torch,sys\n"
                "class Model(torch.nn.Module):\n def __init__(self):\n  super().__init__();self.head=torch.nn.Linear(1,1);self.encoder=torch.nn.Linear(1,1)\n"
                "m=Model();state=torch.load(sys.argv[2],weights_only=True);m.load_state_dict({k:v+1 for k,v in state.items()});o=torch.optim.AdamW(m.parameters())\n"
                "m.head(m.encoder(torch.ones(1,1))).sum().backward();o.step()\n")
            misc=types.ModuleType('misc');misc.__path__=[]
            cleaner=types.ModuleType('misc.ckpt_clean');cleaner.start_cleaner_thread=lambda:None
            with patch.dict(sys.modules,{'misc':misc,'misc.ckpt_clean':cleaner}),patch.dict(os.environ,{'SDM_TELEMETRY_ROOT':str(root)}):
                with self.assertRaisesRegex(ValueError,'not_from_common_baseline'):entry.run(target,['--load-from',str(baseline)])

    def test_final_export_uses_rename_and_rejects_cap_or_disk_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'epoch_3.pth';destination=root/'checkpoint.pth'
            value=dict(state_dict={'w':torch.tensor(1.)},optimizer={},scheduler={},epoch=3,global_iter=24)
            safety.verified_checkpoint_save(torch,torch.save,value,source)
            with self.assertRaisesRegex(safety.StorageProtectionError,'output_cap'):
                safety.export_verified_checkpoint(source,destination,root,maximum_bytes=1)
            self.assertTrue(source.exists());self.assertFalse(destination.exists())
            with patch.object(safety.os,'replace',side_effect=OSError(errno.ENOSPC,'injected full')):
                with self.assertRaises(OSError):safety.export_verified_checkpoint(source,destination,root)
            self.assertTrue(source.exists());self.assertFalse(destination.exists())
            safety.export_verified_checkpoint(source,destination,root)
            self.assertTrue(destination.exists());self.assertFalse(source.exists())

    def test_directory_monitor_tolerates_atomic_checkpoint_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'stable').write_bytes(b'12345');(root/'moving.tmp').write_bytes(b'123')
            original=Path.lstat
            def renamed(path,*a,**k):
                if path.name=='moving.tmp':raise FileNotFoundError('checkpoint renamed after enumeration')
                return original(path,*a,**k)
            with patch.object(Path,'lstat',renamed):self.assertEqual(safety.directory_bytes(root),5)
            with patch.object(Path,'lstat',side_effect=PermissionError('unreadable quota scope')):
                with self.assertRaises(PermissionError):safety.directory_bytes(root)

    def test_nonfinite_loss_stops_before_backward_even_if_grad_would_be_finite(self):
        import training_entry_v2 as entry
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'train.py'
            target.write_text("import torch\np=torch.tensor(1.,requires_grad=True)\n(p*0+float('inf')).backward()\n")
            misc=types.ModuleType('misc');misc.__path__=[]
            cleaner=types.ModuleType('misc.ckpt_clean');cleaner.start_cleaner_thread=lambda:None
            with patch.dict(sys.modules,{'misc':misc,'misc.ckpt_clean':cleaner}),patch.dict(os.environ,{'SDM_TELEMETRY_ROOT':str(root)}):
                with self.assertRaisesRegex(FloatingPointError,'nonfinite_loss_before_backward'):entry.run(target,[])


if __name__=='__main__':unittest.main()
