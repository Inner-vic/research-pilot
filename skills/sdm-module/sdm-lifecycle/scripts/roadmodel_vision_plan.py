"""Build two isolated, reviewable RoadModel vision recipes from a proven runtime.

This prepares files only. Platform mutations use the separately reviewed manager.
"""
import argparse
import contextlib
import base64
import hashlib
import json
import io
import lzma
from pathlib import Path, PurePosixPath
import shutil


def replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('frozen_source_anchor_mismatch:' + old[:80])
    return text.replace(old, new)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def outcome_sibling(original_path, name):
    """Retain the explicitly bound remote output parent, never a developer default."""
    path=PurePosixPath(original_path)
    if not path.is_absolute() or '..' in path.parts or path.parent==PurePosixPath('/') or PurePosixPath(name).name!=name:
        raise ValueError('bound_remote_output_path_required')
    return str(path.parent/name)


def build(baseline, output):
    from lpai_manager import Manager, digest_file
    script = Path(__file__).resolve().parent
    if output.exists() and any((output / a / 'component-config.json').exists() for a in ('cbb1', 'dinov3')):
        raise ValueError('prepared_attempt_is_immutable_create_new_attempt')
    output.mkdir(parents=True, exist_ok=True)
    manager = json.loads((baseline / 'manager.json').read_bytes())
    baseline_plan = json.loads((baseline / 'plan.json').read_bytes())
    manager['state_root'] = str(output / 'state')
    save(output / 'manager.json', manager)
    group = {'schema': 'roadmodel-vision-comparison/v1', 'baseline_task_uid': '04207807-a415-41e4-bfef-709800bed3f5',
             'peak_gpus': 64, 'per_arm_gpus': 32, 'max_tasks': 2, 'per_arm_process_cap_seconds': 172800,
             'aggregate_gpu_hour_process_cap_including_kill_grace': 64 * 172830 / 3600,
             'queue_startup_release_time_excluded': True, 'price': 'unknown; GPU hours are exposure, not invoice',
             'partial_start_policy': 'finish-peer; either arm remains independently useful against the completed baseline',
             'peer_failure_policy': 'finish-peer; fail-fast within each four-node arm; no replacement or automatic retry',
             'arms': {}, 'qualification': 'First real updates/gradient audit are in-task integration evidence; no prior GPU qualification claimed',
             'comparison': 'two strategy interventions against the existing baseline, not a strict single-factor A/B between different architectures'}
    for arm in ('cbb1', 'dinov3'):
        dest = output / arm
        runtime = dest / 'runtime'
        runtime.mkdir(parents=True, exist_ok=False)
        name = 'autoopt-vision-' + arm + '-80e-32a800-20261102'
        for path in (baseline / 'runtime').glob('*.py'):
            shutil.copyfile(path, runtime / path.name)
        shutil.copyfile(script / 'roadmodel_vision_backbone.py', runtime / 'roadmodel_vision_backbone.py')
        shutil.copyfile(script / 'roadmodel_vision_prepare.py', runtime / 'roadmodel_vision_prepare.py')
        profile = (runtime / 'run_profile.py').read_text()
        profile = replace(profile, "EXPERIMENT = 'a800-formal80-memfix-32gpu-20261031'", 'EXPERIMENT = ' + repr(name))
        (runtime / 'run_profile.py').write_text(profile, encoding='utf-8')
        entry = (runtime / 'fresh_entry.py').read_text()
        entry = replace(entry, 'from fresh_optimization import install_fresh_loader,instrument',
                        'import roadmodel_vision_backbone\nfrom fresh_optimization import install_fresh_loader,instrument')
        entry = replace(entry, "text=observe(instrument(original.decode('utf8')))",
                        "text=observe(instrument(original.decode('utf8')))\n"
                        "old='                    if surface_miou > float(best_epoch.get(\\\"surface_miou\\\", 0.0)):'\n"
                        "new='                    vision_score = roadmodel_vision_backbone.fixed_eight_score(surface_stats)\\n                    if vision_score > float(best_epoch.get(\\\"surface_fixed8_miou\\\", -1.0)):\\n                        best_epoch[\\\"surface_fixed8_miou\\\"] = vision_score'\n"
                        "assert text.count(old)==1\ntext=text.replace(old,new)\n"
                        "text='import roadmodel_vision_backbone\\n'+text")
        (runtime / 'fresh_entry.py').write_text(entry, encoding='utf-8')
        optimization = (runtime / 'fresh_optimization.py').read_text()
        optimization = replace(optimization, '        clock=UpdateClock(optimizer,scheduler,torch,control)',
                                '        from roadmodel_vision_backbone import audit_optimizer\n'
                                '        optimizer._vision_audit = audit_optimizer(optimizer, model, control)\n'
                                '        clock=UpdateClock(optimizer,scheduler,torch,control)')
        if arm == 'dinov3':
            optimization = replace(optimization, "if sha!='acf61dfa9d2a1ad2ad2b8b6d462e8983b528d7a396c9edeca6a65d40b7d6afcb':", "if sha!='ed8a1265847086c913535fe7d84c0156a68dc98c0edd52298b8bb4bb7bdcbbb1':")
            optimization = replace(optimization, '        result=original(cfg,args,model,optimizer,scheduler,quant_spec,train_loader,vision_loader,logger,**kwargs)',
                                    '        img_argument=args.img_backbone_pretrain\n        args.img_backbone_pretrain=""\n'
                                    '        try:\n            result=original(cfg,args,model,optimizer,scheduler,quant_spec,train_loader,vision_loader,logger,**kwargs)\n'
                                    '        finally:\n            args.img_backbone_pretrain=img_argument')
            start = optimization.index('        saved=torch.load(path,')
            end = optimization.index('        updates=math.ceil', start)
            optimization = optimization[:start] + (
                "        expected=torch.load(path,map_location='cpu',weights_only=True)\n"
                "        actual=model.img_backbone.encoder.state_dict()\n"
                "        if set(expected)!=set(actual) or not all(torch.equal(actual[k].detach().cpu(),v) for k,v in expected.items()):\n"
                "            raise ValueError('dinov3_applied_state_mismatch')\n"
                "        if any(p.requires_grad for p in model.img_backbone.encoder.parameters()):\n"
                "            raise ValueError('dinov3_encoder_unexpectedly_trainable')\n"
                "        counts={'dinov3_encoder':len(expected),'new_projection_tensors':len(model.img_backbone.projections.state_dict())}\n"
            ) + optimization[end:]
        (runtime / 'fresh_optimization.py').write_text(optimization, encoding='utf-8')
        launch = (runtime / 'normal_training_a800_fixed.py').read_text()
        config_insert = " c.optimizer['paramwise_cfg']['custom_keys']['img_backbone']['lr_mult']=1.0;"
        if arm == 'dinov3':
            config_insert += " c.model['img_backbone']=dict(type='DINOv3ImageBackbone',checkpoint=__import__('os').environ['FRESH_BACKBONE'],embed_dims=128);"
            launch = replace(launch, "        checkpoint = pathlib.Path(os.environ['LPAI_INPUT_MODEL_4']) / 'best.pth'",
                             "        from roadmodel_vision_prepare import prepare\n"
                             "        vendor, weight_path = prepare(pathlib.Path(tempfile.mkdtemp(prefix='sdm-vision-inputs-')), pathlib.Path(os.environ['LPAI_INPUT_MODEL_5']) / 'pytorch_model.bin')\n"
                             "        env['PYTHONPATH'] = vendor + os.pathsep + str(pathlib.Path(__file__).parent) + os.pathsep + env.get('PYTHONPATH','')\n"
                             "        checkpoint = pathlib.Path(weight_path)")
            launch = replace(launch, "sha(checkpoint) == 'acf61dfa9d2a1ad2ad2b8b6d462e8983b528d7a396c9edeca6a65d40b7d6afcb'",
                             "sha(checkpoint) == 'ed8a1265847086c913535fe7d84c0156a68dc98c0edd52298b8bb4bb7bdcbbb1'")
            launch = replace(launch, "        env.update(RECOVERY_SOURCE=str(source),", "        env['FRESH_BACKBONE'] = str(checkpoint)\n        env.update(RECOVERY_SOURCE=str(source),")
            launch = replace(launch, "        model_args = ['--img-backbone-pretrain', str(checkpoint)]",
                             "        run([sys.executable, str(pathlib.Path(__file__).with_name('roadmodel_vision_backbone.py')), '--checkpoint', str(checkpoint), '--output', str(OUT / 'dino-gpu-preflight.json')], source, env, 'dino-gpu-preflight')\n"
                             "        model_args = ['--img-backbone-pretrain', str(checkpoint)]")
        launch = replace(launch, "c.SDM_SCHEDULER_EPOCHS=80;", "c.SDM_SCHEDULER_EPOCHS=80;" + config_insert)
        launch = replace(launch, "assert int(r.train_loader['batch_size'])==8", "assert r.optimizer['paramwise_cfg']['custom_keys']['img_backbone']['lr_mult']==1.0; assert int(r.train_loader['batch_size'])==8")
        (runtime / 'normal_training_a800_fixed.py').write_text(launch, encoding='utf-8')
        for path in runtime.glob('*.py'):
            compile(path.read_text(encoding='utf-8'), str(path), 'exec')
        files = {p.name: p.read_text(encoding='utf-8') for p in sorted(runtime.glob('*.py'))}
        raw = json.dumps(files, separators=(',', ':'), ensure_ascii=True).encode()
        payload_sha = hashlib.sha256(raw).hexdigest()
        component = json.loads((baseline / 'component-config.json').read_bytes())
        component.update(name=name, describe=f'Vision {arm};80epochs32A800 physical8acc1global256;fresh;fixed8 selector;48hcap;retry0')
        if arm == 'dinov3':
            component['input_model'] = [{'env_key': 'LPAI_INPUT_MODEL_5',
                'env_value': '/lpai/inputs/models/dinov3-vitb16-25-09-24-2241',
                'model_files': 's3://tos-s3-cn-beijing.volces.com/prod-lpai-asset/model/timm__vit_base_patch16_dinov3_lvd1689m/25-09-24-2241-20260728183217/pytorch_model.bin',
                'model_set': 'timm__vit_base_patch16_dinov3_lvd1689m', 'model_version': '25-09-24-2241'}]
        component['envs'] = {'A800_recovery_packed': base64.b64encode(lzma.compress(raw)).decode()}
        component['cmd'] = ("timeout --signal=TERM --kill-after=30s 172800s python - <<'VISION_PAYLOAD'\n"
                            "import base64,lzma,hashlib,json,pathlib,runpy,tempfile,sys,os;os.environ['SDM_OUTER_QUALIFICATION']='0';"
                            "raw=lzma.decompress(base64.b64decode(os.environ['A800_recovery_packed'],validate=True));"
                            f"assert hashlib.sha256(raw).hexdigest()=='{payload_sha}';"
                            "p=pathlib.Path(tempfile.mkdtemp(prefix='a800-vision-'));files=json.loads(raw);"
                            "[(p/n).write_bytes(t.encode()) for n,t in files.items()];sys.path.insert(0,str(p));"
                            "runpy.run_path(str(p/'normal_training_a800_fixed.py'),run_name='__main__')\nVISION_PAYLOAD")
        save(dest / 'component-config.json', component)
        info = {'name': name, 'problem_statement': '固定八类 surface mIoU 在完成80轮后约45%，稀有类仍弱，需要区分视觉微调不足与视觉表征限制。',
                'solution_approach': ('保留CBB1，将视觉主干lr_mult从0.1提高到1.0；其余80轮配置、batch256与全量数据保持。' if arm == 'cbb1' else '用冻结DINOv3 ViT-B/16及可训练三尺度适配层替换CBB1；不裁剪缩放原图，保留LiDAR/BEV/head/loss和batch256。'),
                'expected_result': '产出完整逐类IoU、固定八类mIoU、带支持量高度误差、按固定八类保留的最佳权重和末两轮；改善是假设，pillar正式M1仍不可用。',
                'expected_duration': 1200 if arm == 'cbb1' else 2400,
                'expected_duration_desc': ('同32A800/80轮历史实际18h42m，计划20h，不含排队准备。' if arm == 'cbb1' else 'DINO高分辨率注意力、逐摄像头分块的速度未实测，40h仅保守排期；48h封顶可能无法完成80轮。') + '独立48h进程上限+30s强杀、retry0；两组同时最大64卡约3072.53GPUh进程暴露，不含排队/启动/平台释放延迟。'}
        save(dest / 'task-info.json', info)
        request = {'action': 'component.create', 'request_key': name + '-component', 'name': name,
                   'transport': 'official-sdk', 'sdk_config': component}
        native = Manager(manager)
        # Reuse the official SDK in this already isolated SDK interpreter.
        # Package discovery can exceed the generic child's 60-second limit.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            native.backend.sdk()
            plan = native.plan(request)
        if plan['preview']['requested_gpus'] != 32:
            raise ValueError('expected32gpu_per_arm')
        save(dest / 'create-plan.json', plan)
        save(dest / 'create-body.json', plan['request']['body'])
        if len(json.dumps(plan['request']['body']['data']).encode()) > 60000:
            raise ValueError('component_payload_too_large')
        metadata = {'arm': arm, 'name': name, 'source_commit': 'fca04db7e1f275d08e9fc83c865cb62893c5c675',
                    'baseline_runtime': str(baseline), 'nodes': 4, 'gpus_per_node': 8, 'total_gpus': 32,
                    'batch_per_gpu': 8, 'gradient_accumulation': 1, 'nominal_effective_batch': 256,
                    'end_epoch': 80, 'scheduler_design_epochs': 80, 'warmup_epochs': 3, 'base_lr': 2e-5,
                    'img_backbone_lr_mult': 1., 'minimum_lr_ratio': .1, 'seed': 42,
                    'initialization': 'fresh task from verified image pretraining, not resume of epoch80',
                    'selector': 'max fixed8 surface mIoU; raw dynamic mean and height logged independently',
                    'fixed8_class_ids': [1, 2, 16, 17, 23, 4, 24, 25],
                    'allocation_wall_cap_seconds': 172800, 'retry': 0,
                    'runtime_sha256': {n: hashlib.sha256(t.encode()).hexdigest() for n, t in files.items()},
                    'payload_sha256': payload_sha, 'outcome_path': outcome_sibling(baseline_plan['outcome_path'], name),
                    'create_plan_sha256': digest_file(dest / 'create-plan.json')}
        save(dest / 'plan.json', metadata)
        group['arms'][arm] = metadata
    save(output / 'group-plan.json', group)
    print(json.dumps({'status': 'prepared', 'platform_writes': 0, 'peak_gpus': 64, 'arms': list(group['arms'])}))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    build(a.baseline.resolve(), a.output.resolve())
