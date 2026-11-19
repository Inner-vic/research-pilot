"""Create original SDM contract objects from the qualified binary adapter facts.

An authoring adapter, not an acceptance engine. Every generated object goes
through the same original validators as explicitly supplied objects.
"""
import hashlib
from pathlib import Path

from canonical import digest
from component_research import reference
from research_intent import _record


CONTRACT_IDS = ['G01', 'G02', 'G03', 'G07', 'G08', 'G09', 'G10', 'G13']


def assemble(spec, intent):
    value=spec['sdm_governance']
    if set(value)!={'schema','data_source','max_submissions','variables','author_principal_ids'}:
        raise ValueError('explicit_component_governance_authoring_inputs_required')
    from prepare_public_ts import metadata
    raw=reference(value['data_source']).read_bytes()
    info=metadata(raw,spec['comparison_identity']['protocol'])
    project=spec['project_id'];created=intent['requirement']['created_at']
    def key(kind):return kind.lower()+'-'+project
    def record(kind,content):
        return _record(kind,key(kind),content,project,created)
    source={'S02:'+intent['brief']['instance_key']:digest(intent['brief'])}
    def contract(kind,content):return record(kind,{'source_refs':source,**content})
    contracts={
        'G01':contract('G01',{'principles':['Account every submitted Attempt including failures and unknowns.',
            'Keep metric/evaluation identity and original execution authority fixed.'],
            'separation_of_duties':{'required':True,'proposal':'generator','review':'distinct critic','execution':'original Manager/Guard'},
            'freeze_policy':{'immutable':True,'basis':'Pinned runtime, inputs and accepted plans'},
            'exception_policy':{'authority':'required','missing_evidence':'block; no automatic override'}}),
        'G03':contract('G03',{'dataset_identity':'sha256:'+hashlib.sha256(raw).hexdigest(),
            'version':hashlib.sha256(raw).hexdigest(),
            'schema':{'signal':'finite float32 univariate length '+str(info['length']),'label':'binary integer'},
            'quality_policy':{'fixed_length':info['length'],'finite_numeric_input':True,
                'partition':'Deterministic stratified training/validation, disjoint duplicate signals'},
            'lineage':{'source':value['data_source'],'upstream':spec['workload']['source_ref']}}),
        'G07':contract('G07',{'ontology_identity':project+'-binary-labels','classes':['negative','positive'],
            'semantics':{'negative':'Raw label '+repr(info['class_labels'][0]),'positive':'Raw label '+repr(info['class_labels'][1])},
            'version':spec['comparison_identity']['source_sha256']}),
        'G08':contract('G08',{'operations':[{'operation_id':'identity','type':'identity'}],
            'probabilities':{'identity':1.0},'ordering':['identity'],
            'geometry_effects':{'identity':{'signal':'unchanged','labels':'unchanged'}}}),
        'G09':contract('G09',{'inputs':['signal'],'outputs':['logits'],
            'shape':{'signal':[1,info['length']],'logits':[2]},'dtype':'float32',
            'units':{'signal':'source units, unchanged','logits':'dimensionless'},
            'runtime_validator_digest':'sha256:'+hashlib.sha256(Path(__file__).with_name('component_preflight.py').read_bytes()).hexdigest()}),
        'G10':contract('G10',{'evaluator_identity':spec['comparison_identity']['protocol'],
            'metric_definitions':{'macro_f1':'Unweighted mean of binary per-class F1; zero denominator contributes zero.',
                'accuracy':'Number of correct validation predictions divided by validation count.'},
            'slices':['fixed-validation'],'tolerances':{'metric_recomputation':1e-10},
            'inconclusive_rule':'Adaptive validation lacks confirmatory evidence; the original decision evaluator returns inconclusive.',
            'owner_principal_ids':value['author_principal_ids']}),
        'G13':contract('G13',{'node_types':['Plan','Run','Attempt','Result'],
            'edge_types':['uses','used-by'],'reciprocity':{'uses':'used-by'},'closure':{'required':True}})}
    contracts['G02']=contract('G02',{'references':sorted(k+':'+v['instance_key'] for k,v in contracts.items()),
        'source_refs':{k+':'+v['instance_key']:digest(v) for k,v in contracts.items()}})
    baseline=record('B01',{'baseline_id':key('B01'),'scope':'Define the exploration starting recipe and fixed comparison scope; actual results support later reference selection, not automatic reproduction or baseline-lock qualification.',
        'model':'sha256:'+spec['baseline_model']['sha256'].removeprefix('sha256:'),
        'data':'G03:'+key('G03'),'evaluator':'G10:'+key('G10'),'tolerances':{'metric_recomputation':1e-10}})
    experiment={'experiment_id':project+'-experiment','protocol_channel':'exploratory','promotable':False,
        'hypothesis':intent['brief']['content']['hypothesis'],'variables':value['variables'],
        'budget':{'max_submissions':value['max_submissions']},'author_principal_ids':value['author_principal_ids'],
        'decision_rule':{'schema':'sdm-scientific-rule/v1','support_predicate':'confirmatory_support',
            'rejection_predicate':'confirmatory_rejection','inconclusive_rule':'underpowered_or_no_decisive_result','invalid_rule':'protocol_invalid'}}
    omitted={
        'G04':'No spatial frames or geometric transformations are present in the univariate classification adapter.',
        'G05':'No calibrated sensor geometry or calibration-dependent scientific claim is used by this adapter.',
        'G06':'The accepted input is one univariate sequence without cross-sensor timestamps or synchronization claims.',
        'G11':'Only fixed-validation exploratory quality is assessed; robustness is not claimed and would require another adapter.',
        'G12':'No staging, device, latency, health or production deployment qualification is performed or claimed.'}
    return {'schema':value['schema'],'records':{'contracts':contracts,'baseline':baseline,'experiment':experiment},
            'not_applicable':omitted,'data_source':value['data_source'],'max_submissions':value['max_submissions']}
