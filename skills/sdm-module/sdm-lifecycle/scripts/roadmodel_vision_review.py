"""Freeze source-backed vision comparison evidence for the public SDM review."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path

from proposal_review import subject
from task_pipeline import digest
from research_model_runner import shared_evidence, guidance_sources
from review_storage import atomic_json


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(directory, baseline):
    if (directory / 'review/FROZEN.json').exists():
        raise ValueError('immutable_review_already_frozen')
    if json.loads((directory / 'tests.json').read_bytes())['passed'] is not True:
        raise ValueError('actual_tests_must_pass')
    script = Path(__file__).resolve().parent
    policy = json.loads((baseline / 'review-project.json').read_bytes())['proposal_review']
    policy['model_runner'].update(cwd=str(directory), timeout_seconds=600, startup_timeout_seconds=120)
    policy['model_runner']['cli_sha256'] = sha(Path(policy['model_runner']['cli_path']))
    cfg = {'proposal_review': policy, 'authorization': {'confirmed': True, 'source_ref': str(directory / 'AUTHORIZATION.md'),
           'scope': 'User explicitly requests two concurrent component-library experiments: CBB1 finetuning and DINOv3-B16 replacement. Prior material-sharing authorization to configured gpt-6-sol and gpt-6-astra persists.'},
           'component_execution_plan': {'path': str(directory / 'group-plan.json'), 'sha256': sha(directory / 'group-plan.json')},
           'contract_tests': {'path': str(directory / 'tests.json'), 'sha256': sha(directory / 'tests.json')}}
    cfg['research_observation'] = {'version': 1, 'project_id': 'autoopt-roadmodel-vision-20261102',
        'question_id': 'q-vision-quality', 'store': str(directory / 'research.sqlite3'),
        'run_roots': [str(directory)]}
    atomic_json(directory / 'review-project.json', cfg)
    bound = digest(subject(cfg))
    review = directory / 'review'
    out = review / 'sources'
    out.mkdir(parents=True)
    sources = []
    def add(name, raw, kind):
        raw = raw.encode() if isinstance(raw, str) else raw
        offset = 0
        index = 0
        while offset < len(raw):
            end = min(offset + 14000, len(raw))
            if end < len(raw):
                end = raw.rfind(b'\n', offset, end) + 1
            if end <= offset:
                raise ValueError('review_source_line_unbounded')
            index += 1
            target = out / (name + '-' + str(index) + '.txt')
            target.write_bytes(raw[offset:end])
            offset = end
            sources.append({'id': target.stem, 'kind': kind, 'path': str(target), 'sha256': sha(target)})
    def files(name, paths, kind):
        add(name, b''.join(('# FILE ' + p.name + '\n').encode() + p.read_bytes() + b'\n' for p in paths), kind)
    runtime = directory / 'dinov3/runtime'
    files('new-runtime', [runtime / n for n in ('roadmodel_vision_backbone.py', 'roadmodel_vision_prepare.py',
          'fresh_entry.py', 'fresh_optimization.py', 'normal_training_a800_fixed.py', 'run_profile.py')], 'code')
    differing = []
    inherited = {}
    for name in sorted(p.name for p in runtime.glob('*.py')):
        a, b = (directory / 'cbb1/runtime' / name), runtime / name
        if a.read_bytes() != b.read_bytes():
            differing.extend(difflib.unified_diff(b.read_text().splitlines(True), a.read_text().splitlines(True),
                             fromfile='dinov3/' + name, tofile='cbb1/' + name))
        old = baseline / 'runtime' / name
        if old.exists() and old.read_bytes() == a.read_bytes() == b.read_bytes():
            inherited[name] = sha(old)
    add('arm-differences', ''.join(differing), 'code')
    public = [script / n for n in ('roadmodel_vision_dispatch.py',
              'lpai_task_inventory.py', 'check_roadmodel_vision.py', 'roadmodel_vision_stage_check.py')]
    files('drivers-and-tests', public, 'code')
    proof = {'inherited_unchanged_runtime': inherited, 'baseline_task_uid': '04207807-a415-41e4-bfef-709800bed3f5',
             'baseline_actual': 'Succeeded80epochs/all32rank actualupdates/all80 distributed validations/finalcheckpointCPUhashreload/all4userpodsSucceededexit0. Runtime18h42m02s. Priorworking-setmemoryfix completedlongrun; no newGPUfitclaim forDINO.',
             'baseline_review_validation': json.loads((baseline / 'review-validation.json').read_bytes()),
             'modified_runtime_inlined': 'fullDINOmodifiedruntime plus exactCBBdifference; unchangedhelperbytes boundbyhash to actualsuccessful baseline',
             'no_platform_writes_yet': True}
    atomic_json(directory / 'runtime-provenance.json', proof)
    files('recipe-and-evidence', [directory / n for n in ('group-plan.json', 'AUTHORIZATION.md', 'tests.json', 'runtime-provenance.json', 'public-asset-detail.utf8.json', 'comparison-policy.json', 'previous-review.json', 'stage-tests.json', 'review-launcher-recovery.json')], 'artifact')
    for arm in ('cbb1', 'dinov3'):
        body = json.loads((directory / arm / 'create-body.json').read_bytes())
        packed = body['data']['lpai_envs'][0]['value']
        body['data']['lpai_envs'][0]['value'] = {'omitted_encoded_payload': True, 'encoded_sha256': hashlib.sha256(packed.encode()).hexdigest(),
            'decoded_runtime_sha256': json.loads((directory / arm / 'plan.json').read_bytes())['runtime_sha256']}
        body['data']['lpai_envs'][0]['value'].pop('decoded_runtime_sha256')
        body['data']['lpai_envs'][0]['value']['decoded_runtime_manifest_ref'] = 'group-plan.json:arms.' + arm + '.runtime_sha256'
        # Show the exact command once, retaining verified references for the
        # duplicated master/worker copies instead of consuming source budget.
        command = body['data']['lpai_task']['cmd']
        for role in body['data']['lpai_task']['roles']:
            if role['cmd'] != command:
                raise ValueError('role_command_differs_from_task_command')
            role['cmd'] = {'same_as': 'data.lpai_task.cmd', 'sha256': hashlib.sha256(command.encode()).hexdigest()}
        atomic_json(directory / arm / 'deployment-review.json', {'native_payload_projection': body,
            'create_body_sha256': sha(directory / arm / 'create-body.json'), 'actual_task_network': 3, 'catalog_network': 2})
        files(arm + '-deployment', [directory / arm / n for n in ('deployment-review.json', 'task-info.json')], 'config')
    context = {'schema': 'sdm-review-context/v1', 'subject_digest': bound, 'sources': sources,
        'goal': 'Diagnose limited fixed8 surface semantic quality after a successful80epoch baseline. User explicitly requests concurrent implementation/submission of two previously proposed strategies; formal50-100epochs with meaningful batch. Real objectivepillar8IoU>=90%mainline,drivableheightMAE<1cm,10sceneGT/PREDvisual; pillaroutputmissingsoM1unavailable.',
        'constraints': 'Two separate32A800tasks4x8 each, batch8acc1 effective256,80epochs fullcosine80 warm3 LR2e-5 minratio0.1 AMP seed42, same111190train/10568validation frames pinnedindex/source/sourceimage/LiDAR/encoder/gaussianhead/loss. Independent48hprocesscap+30skill/retry0/outputdirs; noautosuccessor/timer. Aggregatepeak64,process exposure3072.53GPUh excludingqueue/startup/release;priceunknown. Usercurrenttwo-task/modelauthorizationoverridessingle-task/head-onlydefaults;priorA/Bmaterial-sharingauthorizationpersists.',
        'data': 'Completedbaseline UID04207807-a415-41e4-bfef-709800bed3f5: fixed8ep31=40.293%,ep60=45.0496%,ep71=45.753%,ep80=45.219%;ep60->80 +0.169pp under80epochcosine,notproofmodelcapacityceiling. DrivableheightMAEep80.610cm supported527650912 points; changingpreddependentmasksso descriptive. Bumper14.292%,manhole23.879%,joint41.373%,asphalt87.485%. RawdynamicmIoUdeclines31.256->26.480 duezeroGTclasseswithfalsepositivesjoiningdenominator;fixedGT15improves31.256->33.541. Bothrawfalsepositivesandfixed8reported;nofalseoverfitclaim.',
        'code_revision': 'FullDINOmodifiedruntimeandexactCBBdiffinlined. CBB1customCNNintegratedneck isactuallytrainable atlr_mult0.1,peak2e-6; globalCosinefloor2e-6makesitsgroupnearlyflatpostwarmup. ArmA changeslr_mult1.0sopeak2e-5anddecay2e-6. ArmBusesstrictlyloadedDINOv3ViTB16 frozenencoder, blocks3/7/11normalizeddensemaps,3new768->128convprojections resizedtothe originalstride8/16/32 cameraimageextents. ExistingRGBImageNetnormalizationmatchespretraining; noresize/crop/intrinsicchange;processonecameraatatimeforattentionmemory. train()reassertsfrozenencoderafterBEVSegmentorunfreezecallandbefor eDDP;projection/baseheadtrainat2e-5. Torch2.5.1CPUtestedwithtimm1.0.22. Auditfirst1/200actualsuccessfulupdateschecksfinite/nonzerovisualgradientsandparameterchange;DINOencoderhasnograd. Newfixed8maximumselectorretainsbest+last2,rawdynamicmeanandheightloggedseparately. NoformalM1or10sceneclaim.',
        'configuration': 'OfficialSDKpackage41=8A80080GB,104CPU872GiBpernode. SDKcreatebodyfits60k;taskcanonicalstoredcomponentdataoverriddenNetwork3only. InputmodelCBBverifiedSHAacf61d...; DINOpublictimmHFcommitc6a5fb7d...binSHAed8a1265...342624338bytes. Publicrepo gatedfalseandlocaldownloadactualhashverified. SharedplatformimportAVAILABLEtargetcluster;assetSDKreturnedactualTOSversionpathafteragentget500. DINOweightisnativeSDKinput_modelwithactualTOSfilepinnedabove; runtimehashesmountedfileandusesboundedexactHTTPSfor2.5MBtimmwheelonly,nofallbackorcredentialtransfer. Isolated--no-depsvendorpathdoesnotupgradeTorchorotherinstalledpackages. Downloadmax600s,filebytebound,http60s; outer48hkill/watchdogboundsstage. ContainerwheelHTTPSnotyetverified;failureexitstasknoretry. Fulloriginal-resolution1cameraGPUforward/backward/updateprobeisrequiredpernodebeforedistributedformaltraining;actual256batchfitremainsfirstupdateevidence. NoGPUdevspaceorqualificationtaskadded.',
        'comparison': 'Primary completed80 final fixed8 vs baseline45.219%; max fixed8 is secondary vs logged45.753%. A promising descriptive screen needs>=1pp final gain,>=2ofbumper/manhole/joint improve,height<1cm,no>=5pp targetclassdrop. Small/mixed changesinconclusive;partialrunnotwinner;single seed42doesnotestimatevariance. Noautomaticpromotion. Two strategy interventions vs existing80epochbaseline,notpureone-factorbetweenAandB: architecture/initialization/newprojections/freeze/vendor-timm differ. Fixeddata/head/batch/epochs/loss/seedheld. FrozenDINOisfeaturetransferprobe; successorfine-tuneunapprovedhere. Newtasksfreshfrompretrain,sameheadrandomseed notresume80. Full80epochsneededfornewadapter;longertrainingdirection notpretendedtestedbythispair. DINO40hplanningonlyunmeasured,mayhit48hcapbefore80;mustreportcompletedepochsandsuccessfulupdates,notclaimformal80completeoncap.',
        'history': 'r5reviewlauncherlocalguardstopped-emptyat30secstartup;nooutput-schema/cli-processes/stdout/backendreadycreated;transportcodewrites schema beforeCLIstart,soevidencesupportsprelocaldispatchfailure,no provider-globalattestation. Oldunknownintentpreservednoreplay;newlinkedr6startupwait120/maxallowed,backend600andcontainmentunchanged;workloadandnativebodybyteidenticaltotheactuallyCPUtestedr5,testsreusedexplicitly. Previouscandidate r4 actualsol/astra review awaited evidence: duplicate GPU probe launches collided on exclusive log open, plus duplicate main blocks. r5 removes duplicate entry/probe, adds actual stage runner with lightweight real child success/failure/duplicate negativecontrol, retains old payload unchanged. Seeprevious-review.json dispositionsC1/C2. Baselinecheckpointbestselectedrawdynamicmeanatcompleted31;fixed8best71notretained. Newselectorcorrectsthis prospectivelybotharms;oldruntime/artifactsunchanged. Sameexisting8GiBoutputcap/1GiBsinglecheckpoint/hashreload/2epochretention andmemoryworking-set90%/visiblefiniteancestors/ownedPIDcleanup/watchdogprogress900s/lowGPU30m/log256MiB/peerabortwithinarm maintained. BaselineGPUrank0allocated22.56GiBreserved37.95GiB;DINOdoesnotinheritthisfitproof. CPUmodeltestsactualpretrainedload,smalldenseforwardbackward/freezeandpayloadpinnedsourceoverlayaretestevidenceonly.',
        'preflight_and_stop': 'ActualindependentGenerator–Criticreviewbeforewrites. Onegroupfamilylockserializescomponentcreate/tasksubmitandledgerwrites;fullownerinventorypreservesallforeignA800conflicts;onlyexactverifiedtwoUIDsinthisgroupexempt. Durableintentbeforewrite,unknownreconciliationonly,noresubmit. Eachcomponent/run/outputisolated. Eachfour-nodearmabortsignalsownpeers;otherarmmayfinish independentlyagainstbaseline. Startupfirstrealupdatesprovideintegrationevidence,notpriorGPUqualification. Ifinput/strictload/freeze/gradient/finite/memory/storage/progress/runtimeboundsfail,taskexitsandpodsrelease;noautomaticreplacement. RecordexactUIDreadback/podstatusafterwrites,releaseonlywhenactualterminal.',
        'unknowns': 'FullbatchGPUfit/48hDINOcompletionandcontainerwheelHTTPSunknown;localCPUtestnotGPUproof. Noimageablation/permodulebaselinegradienthistoricalevidence. BenefitofDINOor10xCBBLRunknownscientificquestion,notprerequisiteconfirmedresult. DINOmodellicenseinpublicrelease;researchexperimentdoesnotclaimproductiondeploymentreadiness. Genericpairedplatformleaseadapternotused; establishedcomponentSDKroute usedwithexactworkloadprocesscap,observedpriorpodsrelease,andexplicitqueue/releaseunknownscope.'}
    atomic_json(review / 'review-context.json', context)
    registered = {s['path']: 'sha256:' + s['sha256'] for s in sources}
    registered[str(review / 'review-context.json')] = 'sha256:' + sha(review / 'review-context.json')
    guidance_sources(registered)
    preview = shared_evidence(subject(cfg), {}, {'subject_digest': bound, 'context': context}, registered, review / 'delivery-preview')
    critical = {s['path'] for s in sources}
    missing = [s['path'] for s in preview['raw_sources'] if s['path'] in critical and s.get('omitted_bytes')]
    if missing:
        raise ValueError('critical_source_truncated:' + str(missing))
    names = ['group-plan.json', 'manager.json', 'AUTHORIZATION.md', 'tests.json', 'runtime-provenance.json', 'review-project.json', 'comparison-policy.json', 'previous-review.json', 'stage-tests.json', 'review-launcher-recovery.json']
    for arm in ('cbb1', 'dinov3'):
        names.extend(arm + '/' + n for n in ('component-config.json', 'create-plan.json', 'create-body.json', 'plan.json', 'task-info.json'))
        names.extend(str(p.relative_to(directory)) for p in (directory / arm / 'runtime').glob('*.py'))
    names.extend(os.path.relpath(p, directory) for p in public)
    names.append(os.path.relpath(Path(__file__).resolve(), directory))
    names.extend(['public-asset-detail.json', 'public-asset-detail.utf8.json'])
    names.append(os.path.relpath(script/'roadmodel_vision_plan.py', directory))
    names.extend(os.path.relpath(script / n, directory) for n in ('roadmodel_vision_backbone.py', 'roadmodel_vision_prepare.py'))
    atomic_json(review / 'FROZEN.json', {'subject_digest': bound, 'sources': sources,
                'artifacts': {n: sha(directory / n) for n in names}})
    print(json.dumps({'status': 'frozen', 'subject_digest': bound, 'sources': len(sources), 'critical_sources_omitted': missing}))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--baseline', type=Path, required=True)
    a = p.parse_args()
    freeze(a.directory.resolve(), a.baseline.resolve())
