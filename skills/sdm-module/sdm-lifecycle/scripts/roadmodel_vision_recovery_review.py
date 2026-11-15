"""Freeze an engineering repair of one arm for the public independent review."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from proposal_review import subject
from research_model_runner import guidance_sources, shared_evidence
from review_storage import atomic_json
from task_pipeline import digest


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(directory, parent):
    if (directory / 'review/FROZEN.json').exists():
        raise ValueError('immutable_review_exists')
    assert json.loads((directory / 'tests.json').read_bytes())['passed']
    scripts = Path(__file__).resolve().parent
    policy = json.loads((parent / 'review-project.json').read_bytes())['proposal_review']
    policy['model_runner'].update(cwd=str(directory), startup_timeout_seconds=120, timeout_seconds=600)
    policy['model_runner']['cli_sha256'] = sha(Path(policy['model_runner']['cli_path']))
    cfg = {'proposal_review': policy,
        'authorization': {'confirmed': True, 'source_ref': str(directory / 'AUTHORIZATION.md'),
            'scope': 'Current user explicitly requests analyze failed component and resubmit. Exactly one DINO replacement; existing CBB1 untouched. Prior material-sharing permission to configured sol/astra persists.'},
        'component_execution_plan': {'path': str(directory / 'group-plan.json'), 'sha256': sha(directory / 'group-plan.json')},
        'contract_tests': {'path': str(directory / 'tests.json'), 'sha256': sha(directory / 'tests.json')},
        'research_observation': {'version': 1, 'project_id': 'autoopt-vision-offline-recovery-20261102',
            'question_id': 'q-offline-recovery', 'store': str(directory / 'research.sqlite3'), 'run_roots': [str(directory)]}}
    atomic_json(directory / 'review-project.json', cfg)
    bound = digest(subject(cfg))
    review = directory / 'review'
    sources = []
    out = review / 'sources'
    out.mkdir(parents=True)
    def add(label, paths, kind):
        raw = b''.join(('# FILE ' + p.name + '\n').encode() + p.read_bytes() + b'\n' for p in paths)
        start, index = 0, 0
        while start < len(raw):
            end = min(start + 14000, len(raw))
            if end < len(raw):
                end = raw.rfind(b'\n', start, end) + 1
            if end <= start:
                raise ValueError('unbounded_review_line')
            index += 1
            path = out / (label + '-' + str(index) + '.txt')
            path.write_bytes(raw[start:end])
            sources.append({'id': path.stem, 'kind': kind, 'path': str(path), 'sha256': sha(path)})
            start = end
    runtime = directory / 'dinov3/runtime'
    add('repair-runtime', [runtime / n for n in ('roadmodel_vision_prepare.py', 'normal_training_a800_fixed.py', 'run_profile.py')], 'code')
    drivers = [scripts / n for n in ('roadmodel_vision_recovery.py', 'roadmodel_vision_dispatch.py',
               'lpai_task_inventory.py', 'check_roadmodel_offline_dependency.py', 'roadmodel_vision_stage_check.py', 'check_roadmodel_replacement_admission.py')]
    add('dispatch-and-tests', drivers, 'code')
    add('failure', [directory / 'failure-analysis.json', directory / 'failure-excerpts.txt'], 'log')
    add('recipe', [directory / n for n in ('group-plan.json', 'AUTHORIZATION.md', 'tests.json', 'change-manifest.json', 'admission-tests.json', 'prior-review.json')], 'config')
    add('dependency', [directory / n for n in ('asset-version.utf8.json', 'asset-roundtrip-verification.json')], 'artifact')
    add('parent', [parent / 'review-validation.json', parent / 'tests.json', parent / 'comparison-policy.json'], 'artifact')
    body = json.loads((directory / 'dinov3/create-body.json').read_bytes())
    env = body['data']['lpai_envs'][0]
    env['value'] = {'encoded_sha256': hashlib.sha256(env['value'].encode()).hexdigest(),
                    'manifest': 'group-plan.json arms.dinov3.runtime_sha256'}
    cmd = body['data']['lpai_task']['cmd']
    for role in body['data']['lpai_task']['roles']:
        assert role['cmd'] == cmd
        role['cmd'] = {'same_as': 'data.lpai_task.cmd', 'sha256': hashlib.sha256(cmd.encode()).hexdigest()}
    atomic_json(directory / 'deployment-review.json', {'native_projection': body,
        'full_native_body_sha256': sha(directory / 'dinov3/create-body.json'), 'task_network_override': 3})
    add('deployment', [directory / 'deployment-review.json', directory / 'dinov3/task-info.json'], 'config')
    context = {'schema': 'sdm-review-context/v1', 'subject_digest': bound, 'sources': sources,
        'goal': 'User explicitly asks analyze one failed component and resubmit. Engineering repair only: DINO failed before model/training, while peer CBB1 is running. Existing scientific hypothesis/recipe and previous comparison policy remain unchanged. Need one reliable DINO formal80 trial, not a new model search.',
        'constraints': 'Exactly one new DINO task32A8004x8,80epochs,physicalbatch8acc1global256,baseLR2e-5,warmup3,cosine80,minratio0.1,AMPseed42/full pinned data/source/frozenDINO-B16+projections and unchanged downstream/loss. Existing CBB1 UID7bc74554-c09b-4629-bf3e-47f11396c035 stays intact. Combined active peak64cards. Replacement48hprocesscap+30skill~1536.27GPUh excludingqueue/startup/release;priceunknown. Prior failed task recorded11sec, no successful model updates. No auto retry/successor/timer. Current user explicitly authorizes this manual replacement.',
        'data': 'Official exactUID task/pod reads: DINO eab34aa1-949a-4d8f-85c1-f700d056615d failed; all4usertrainingpods terminal exit1, three deleted/oneFailed. Source/index checks passed onmasterandworker; both same urllib HTTP403 in roadmodel_vision_prepare.fetch(WHEEL) at files.pythonhosted.org, before GPU discovery/model construction/torchrun. Not a CUDA OOM, batch/epoch or convergence failure. Healthy CBB1 has4runninguserpods, zero-basedepoch16 and7149successfulupdates in retainedsnapshot. Error excerpts are complete for this causal stack; raw originals remain local and are not necessary to expose large platform environment dumps.',
        'code_revision': 'Replace runtime network fetching with platform-mounted wheel+checkpoint, verify exact size/SHA beforepip. Only preparation, its one extraenv6 argument and freshexperimentidentity differ; other10runtimefiles unchanged. Wheel1.0.22SHA88898175...,2530238bytes;weightSHAed8a1265...,342624338bytes. Accept exactlyone known file at mountroot ordependency/child, rejectmissing/corrupt/ambiguous inputs. pip--isolated--no-index--no-deps--no-cache-dir--disable-pip-version-check into privatevendor. No network fallback or global dependency upgrade. Original-resolutionGPUprobeandfirst1/200visualgradient/freeze/updateaudits remain mandatory in-task and not yet qualified forDINO.',
        'configuration': 'Project-owned dependency asset autoopt-vision-runtime/timm-1-0-22-20261102 AVAILABLE ontargetcluster. OfficialassetSDK upload+roundtripdownload verifiedsameSHA; actualobjectrelativepathdependency/timm-1.0.22-py3-none-any.whl, newnativeinput_model6pointsatthatexactTOSobject. Existing pretrainedinput5unchanged. Model/data downloads are platformpreparation before workload. Newoutputidentity avoids oldabort/log collision. Resourcepackage418A800/104CPU/872GiB pernode,TaskNetwork3,master1worker3/retry0 unchanged. Compileonly doesnotallocate; exactactualtaskreadbackrequiredafterwrite.',
        'testing': 'Actualpipinstalledwheeloffline twice using exactgeneratedlauncherprepareassignment: oneversion-directorylayoutfromplatformroundtripandoneflattenedfilelayout. Parentnetworkfunctions patchedtofail, unusableHTTP/HTTPSproxyandindexconfigured, pipisolated/noindex/no-deps preventsindex/dependencyrequests; installedmetadata1.0.22checkedfromprivatevendor. Corruptsame-lengthwheel,missingwheelandambiguoustwolocationsrejectedbeforepip. Actualpackagedrunner/lightweightrealWindowschildrenagainverifyonepreflight/log/receipt, trainingboundaryprogress, failurecleanup andduplicate-lognegativecontrol. PriorrealCPU162-tensorpretrainedload/denseforwardbackward/freeze tests reusedbecausemodelbytesunchanged; no newGPUpassclaim.',
        'comparison': 'No scientific setting changed. Completed80 finalfixed8vsbaseline45.219 primary; per-armbest secondary vslogged45.753, partialcannotwin;single seeddescriptive, no varianceclaim orauto-promotion. Heightwithsupport/rawdynamicfalsepositivesstillreported. FormalpillarM1and10scenevisual remainunavailable/unperformed;not claimed by repair.',
        'history': 'r2reviewC2requestedwrite-timefreshness. Newcheck_replacement_live performs exactfailedTask+peer+4roleAPIreadsunderdispatchlockbeforeeachwriteandbeforepersistingnewintent; unreadable/inconsistentdatafailclosed. Fixturesinitializegoodstate,thenoldroleRunningwhileTaskfailedandmissingtask/pods/peerstatusallpreventsubmit;validactualsubmitbranchfixturepasses. NoLPAImutationsinfixtures. Previousrepaircandidateactualsol/astra reviewC1requiredgenericdispatchadmissionenforcement. Newhostguardrejectsabsentormismatchedadmission,bindscurrentreviewsubject/groupplanhash/exactfailed+healthyTaskUID+componentUIDs/one-taskallowance,andonlyallowsfailedDINOwrites. No-initwithterminalCBB,failedTaskwithrunningoldrole,wrongpeer/review/armandnonterminalproofallblockbeforeapply;validinitialize+actualruncreatewithmockAPIpasses. All13workloadfilesandnativebodyunchangedfromactualoffline-testedr1;testsreusedexplicitlyplusnewhosttests. Originaltwo-armfrozenpackage actualsol/astra approval retained. Do not edit either submitted component or frozen runtime. NewgenericrecoveryadmissionfreshchecksfailedUID+all4terminalroles andpeerUIDowner/namespace/componentbindingunderexistingfamilylock, thenimportspeerUIDasexistingonly andmarksfailedarmnew. ExistingdispatchphaseguardsandfullownerinventorypermitonlyexactknownpeerUID, blockforeignA800andunknownwrites. This makesone new DINO task possible without duplicating or restartinghealthyCBB1. AllAPIintents/readbacksretained; unknown mutationsreconcileonly.',
        'unknowns': 'ContainerGPUintegration/full32rankfit/DINOcompletionstillunmeasuredbecauseoldjobneverpasseddependencyfetch; permittedboundedexperimentquestion, not an existingpass. Nativeplatformfiledeliveryhasroundtripassetproof, actualnewprep/modelhashchecksremainneeded. 48h is processlimit,notallocationleaseorinvoicecap; cleanupverifiedfromtask/poddoublesignal, no billingclaim. ExistingmemoryworkingSet90%,storage8GiB/singleckpt1GiB,stage-log256MiB,finite/progress/GPUutil/peerabort/ownedcleanupunchanged.'}
    atomic_json(review / 'review-context.json', context)
    registered = {s['path']: 'sha256:' + s['sha256'] for s in sources}
    registered[str(review / 'review-context.json')] = 'sha256:' + sha(review / 'review-context.json')
    guidance_sources(registered)
    preview = shared_evidence(subject(cfg), {}, {'subject_digest': bound, 'context': context}, registered, review / 'delivery-preview')
    critical = {s['path'] for s in sources}
    omitted = [s['path'] for s in preview['raw_sources'] if s['path'] in critical and s.get('omitted_bytes')]
    if omitted:
        raise ValueError('critical_source_truncated:' + str(omitted))
    artifacts = ['manager.json', 'group-plan.json', 'AUTHORIZATION.md', 'tests.json', 'change-manifest.json',
                 'failure-analysis.json', 'failure-excerpts.txt', 'asset-version.utf8.json', 'admission-tests.json', 'prior-review.json',
                 'asset-roundtrip-verification.json', 'deployment-review.json', 'review-project.json']
    artifacts += ['dinov3/' + n for n in ('component-config.json', 'create-plan.json', 'create-body.json', 'plan.json', 'task-info.json')]
    artifacts += [str(p.relative_to(directory)) for p in runtime.glob('*.py')]
    artifacts += [os.path.relpath(p, directory) for p in drivers + [Path(__file__).resolve()]]
    atomic_json(review / 'FROZEN.json', {'subject_digest': bound, 'sources': sources,
                'artifacts': {n: sha(directory / n) for n in artifacts}})
    print(json.dumps({'status': 'frozen', 'subject_digest': bound, 'critical_sources_omitted': omitted}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--parent', type=Path, required=True)
    args = parser.parse_args()
    freeze(args.directory.resolve(), args.parent.resolve())
