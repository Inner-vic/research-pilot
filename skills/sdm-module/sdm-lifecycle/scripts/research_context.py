"""Durable native Codex context for opt-in event service v2; no model loop."""
from pathlib import Path
import hashlib
import json
from review_storage import atomic_json, locked, read_json


class Context:
    def __init__(self, cfg):
        spec = cfg.get('research_context', {})
        if spec.get('version') != 1 or not Path(spec.get('root', '')).is_absolute():
            raise ValueError('explicit_persistent_research_context_required')
        self.stable_instructions=spec.get('stable_instructions',False)
        if type(self.stable_instructions) is not bool:
            raise ValueError('boolean_stable_native_instructions_policy_required')
        self.first_turn_journal=spec.get('first_turn_journal',False)
        if type(self.first_turn_journal) is not bool:
            raise ValueError('boolean_first_turn_journal_required')
        self.root = Path(spec['root'])
        self.path = self.root/'context.json'
        self.scope = {k: cfg[k] for k in ('project_id', 'cwd', 'codex_home', 'model', 'sandbox')}
        self.scope['model_provider'] = cfg.get('model_provider')
        if self.root.resolve().is_relative_to(Path(cfg['cwd']).resolve()):
            raise ValueError('context_state_must_be_outside_model_writable_workspace')

    def lock(self):
        return locked(self.root, '.context.lock', 'research_context_already_owned')

    def load(self):
        value = read_json(self.path) if self.path.exists() else {'schema':'sdm-research-context/v1', 'scope':self.scope, 'phase':'new'}
        if value.get('schema') != 'sdm-research-context/v1' or value.get('scope') != self.scope:
            raise ValueError('research_context_scope_changed')
        return value

    def open(self, codex, arguments, job_id=None):
        value = self.load()
        if value.get('pending_job') or value['phase'] == 'creating':
            raise ValueError('research_context_unknown_delivery_reconcile_only')
        if self.first_turn_journal and (not isinstance(job_id,str) or not job_id):
            raise ValueError('original_delivery_required_for_first_turn_journal')
        if self.stable_instructions:
            text=arguments.get('developer_instructions','')
            if not isinstance(text,str):raise ValueError('native_developer_instructions_required')
            instruction_sha=hashlib.sha256(text.encode('utf-8')).hexdigest()
            if value['phase']!='new' and value.get('developer_instructions_sha256')!=instruction_sha:
                raise ValueError('native_developer_instructions_changed_or_unbound_new_context_required')
        if value['phase']=='created':
            if not self.empty_for(job_id,value):
                raise ValueError('unstarted_thread_bound_to_original_delivery')
            try:
                thread=codex.thread_resume(value['thread_id'],**arguments)
            except Exception as error:
                # Exact typed provider response, only before any turn intent.
                # Timeouts, auth failures and another ID are never retirement.
                if (type(error).__module__!='openai_codex.errors' or type(error).__name__!='InvalidRequestError'
                        or getattr(error,'code',None)!=-32600
                        or getattr(error,'message',None)!='no rollout found for thread id '+value['thread_id']):
                    raise
                value['empty_threads']=[*value.get('empty_threads',[]),{
                    'thread_id':value['thread_id'],'opening_job':job_id,'reason':'provider-no-rollout-before-turn',
                    'rpc_code':-32600,'no_turn_intent':True}]
                value.pop('thread_id')
                value['phase']='new'
            else:
                if thread.id!=value['thread_id']:raise ValueError('native_resumed_thread_identity_changed')
                return thread
        if value['phase'] == 'new':
            if self.stable_instructions:value['developer_instructions_sha256']=instruction_sha
            if self.first_turn_journal:
                value.update(opening_job=job_id,first_turn_journal_version=1)
            atomic_json(self.path, {**value, 'phase':'creating'})
            thread = codex.thread_start(ephemeral=False, **arguments)
            value.update(phase='created' if self.first_turn_journal else 'ready', thread_id=thread.id)
            atomic_json(self.path, value)
            return thread
        if value['phase'] != 'ready' or not value.get('thread_id'):
            raise ValueError('persistent_thread_identity_required')
        thread = codex.thread_resume(value['thread_id'], **arguments)
        if thread.id != value['thread_id']:
            raise ValueError('native_resumed_thread_identity_changed')
        return thread

    def empty_for(self,job,value=None):
        value=value or self.load()
        return (self.first_turn_journal and value.get('first_turn_journal_version')==1
            and value.get('phase')=='created' and value.get('opening_job')==job and bool(value.get('thread_id'))
            and not any(value.get(k) for k in ('pending_job','pending_turn','last_job','last_turn','receipt')))

    def intent(self, job):
        value = self.load()
        if value['phase']=='created' and not self.empty_for(job,value):
            raise ValueError('unstarted_thread_bound_to_original_delivery')
        if value['phase'] not in ('ready','created') or value.get('pending_job'):
            raise ValueError('native_turn_already_pending_reconcile_only')
        value.update(phase='ready',opening_job=None,pending_job=job, pending_turn=None)
        atomic_json(self.path, value)

    def started(self, job, turn):
        value = self.load()
        if value.get('pending_job') != job or not turn:
            raise ValueError('native_turn_binding_changed')
        value['pending_turn'] = turn
        atomic_json(self.path, value)

    def acknowledge(self, job, receipt):
        """Only a durable exact worker receipt releases the next model delivery."""
        value = self.load()
        raw = Path(receipt).read_bytes(); result = json.loads(raw)
        detail = result.get('detail', {})
        if (result.get('job_id') != job or result.get('status') not in ('completed','failed')
                or detail.get('thread_id') != value.get('thread_id')
                or detail.get('terminal') not in ('completed','failed','interrupted')):
            raise ValueError('durable_native_turn_receipt_required')
        if value.get('last_job') == job and not value.get('pending_job'):
            if value.get('receipt_sha256') != hashlib.sha256(raw).hexdigest():
                raise ValueError('native_turn_receipt_changed')
            return
        if value.get('pending_job') != job or detail.get('turn_id') != value.get('pending_turn'):
            raise ValueError('native_turn_receipt_identity_mismatch')
        value.update(last_job=job, last_turn=value['pending_turn'], pending_job=None, pending_turn=None,
                     receipt=str(Path(receipt).resolve()), receipt_sha256=hashlib.sha256(raw).hexdigest())
        atomic_json(self.path, value)
