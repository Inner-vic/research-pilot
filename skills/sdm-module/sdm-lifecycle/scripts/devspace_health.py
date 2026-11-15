"""Opt-in workload-side health protection; stdlib, no platform or model imports."""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import time
import zipfile


HEALTH_FILES = ['health/status.json', 'health/history.jsonl', 'health/decision.json',
                'health/healthy-checkpoint.bin', 'health/anomaly-checkpoint.bin',
                'health/before.json', 'health/after.json', 'health/comparison.json', 'health/comparison.svg',
                'health/healthy.snapshot.zip', 'health/anomaly.snapshot.zip', 'health/episode.snapshot.zip']


def health_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def snapshot_predictions(root, snapshot, binding):
    try:
        return _snapshot_predictions(root, snapshot, binding)
    except (zipfile.BadZipFile, UnicodeDecodeError, AttributeError, TypeError, RuntimeError, NotImplementedError) as error:
        raise ValueError('invalid_health_snapshot_bundle') from error


def _snapshot_predictions(root, snapshot, binding):
    """Read only the bounded, exact committed pair, never compatibility aliases."""
    name = snapshot['bundle']['path']
    if name not in {'health/healthy.snapshot.zip','health/anomaly.snapshot.zip','health/episode.snapshot.zip'}:
        raise ValueError('unexpected_health_snapshot_path')
    path = Path(root) / name
    if path.resolve() != path or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('bounded_unlinked_health_snapshot_required')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != snapshot['bundle']['sha256']:
        raise ValueError('health_snapshot_digest_changed')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if set(archive.namelist()) != {'checkpoint.bin','predictions.json','snapshot.json'} or len(archive.infolist()) != 3:
            raise ValueError('exact_health_snapshot_members_required')
        if any(info.file_size > 8 * 1024 * 1024 for info in archive.infolist()):
            raise ValueError('bounded_health_snapshot_members_required')
        meta = json.loads(archive.read('snapshot.json'))
        if meta.get('binding') != binding or snapshot.get('binding') != binding or any(meta.get(k) != snapshot.get(k) for k in ('sequence','optimizer_updates','recovery_candidate')):
            raise ValueError('health_snapshot_identity_changed')
        for field, member in [('checkpoint_file','checkpoint.bin'),('predictions_file','predictions.json')]:
            if hashlib.sha256(archive.read(member)).hexdigest() != snapshot[field]['sha256']:
                raise ValueError('health_snapshot_member_digest_changed')
        return json.loads(archive.read('predictions.json'))


def validate_health(policy):
    if policy is None: return
    required = {'version', 'stop_authorized', 'protocol', 'metric', 'direction', 'baseline',
                'failure_boundary', 'minimum_change', 'minimum_samples', 'bad_evaluations',
                'bad_updates', 'max_overflow_skips', 'interval_seconds', 'sample_timeout_seconds', 'checkpoint_file', 'predictions_file'}
    if not isinstance(policy, dict) or not required <= set(policy) or set(policy) - required - {'progress_timeout_seconds'} or policy['version'] != 1 or policy['stop_authorized'] is not True:
        raise ValueError('explicit_health_contract_and_stop_authorization_required')
    if any(not isinstance(policy[k], str) or not policy[k].strip() for k in ('protocol', 'metric')):
        raise ValueError('health_metric_protocol_required')
    if policy['direction'] not in {'maximize', 'minimize'} or any(not health_number(policy[k]) for k in ('baseline', 'failure_boundary', 'minimum_change', 'interval_seconds')):
        raise ValueError('finite_health_bounds_required')
    if policy['minimum_change'] <= 0 or not .02 <= policy['interval_seconds'] <= 60:
        raise ValueError('health_change_and_interval_required')
    if not health_number(policy['sample_timeout_seconds']) or policy['sample_timeout_seconds'] <= policy['interval_seconds']:
        raise ValueError('explicit_health_observation_timeout_required')
    if 'progress_timeout_seconds' in policy and (not health_number(policy['progress_timeout_seconds']) or policy['progress_timeout_seconds'] <= policy['interval_seconds']):
        raise ValueError('explicit_optimizer_progress_timeout_required')
    if ((policy['direction'] == 'maximize' and policy['failure_boundary'] >= policy['baseline']) or
        (policy['direction'] == 'minimize' and policy['failure_boundary'] <= policy['baseline'])):
        raise ValueError('health_boundary_direction_mismatch')
    for k in ('minimum_samples', 'bad_evaluations', 'bad_updates', 'max_overflow_skips'):
        if type(policy[k]) is not int or policy[k] < (2 if k in {'bad_evaluations', 'bad_updates'} else 1):
            raise ValueError('explicit_health_evidence_counts_required')
    for k in ('checkpoint_file', 'predictions_file'):
        name = policy[k]
        if not isinstance(name, str) or not name or Path(name).is_absolute() or any(v in name for v in ('..', ':', '\\')) or name.split('/')[0] in {'health', 'claim'}:
            raise ValueError('safe_health_source_file_required')


class HealthGuard:
    def __init__(self, root, binding, policy):
        validate_health(policy)
        self.root, self.binding, self.policy = Path(root), binding, policy
        self.sequence = -1; self.updates = 0; self.bad_eval = 0; self.bad_update = 0; self.skips = 0
        self.accepted = 0; self.records = 0; self.errors = []; self.healthy = None; self.anomaly = None
        self.last_valid_at = self.last_update_at = time.time()
        self.episode = None; self.snapshot_error = None
        if (self.root / 'health').exists(): raise ValueError('fresh_health_directory_required')
        (self.root / 'health').mkdir()

    def write(self, name, value):
        path = self.root / 'health' / name
        if path.is_symlink(): raise ValueError('linked_health_output_rejected')
        tmp = path.with_suffix('.tmp')
        if tmp.is_symlink(): raise ValueError('linked_health_output_rejected')
        tmp.write_text(json.dumps(value, allow_nan=False), encoding='utf-8'); os.replace(tmp, path)

    def save_sources(self, sample, tag):
        result = {'sequence': sample['sequence'], 'optimizer_updates': sample['optimizer_updates'],
                  'binding': self.binding, 'recovery_candidate': False}
        try:
            sources = {}
            for field, name in [('checkpoint_file', tag + '-checkpoint.bin'), ('predictions_file', 'before.json' if tag == 'healthy' else 'after.json')]:
                source = self.root / self.policy[field]
                if source.resolve() != source or not source.is_file() or source.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError('health_source_missing_linked_or_oversized')
                raw = source.read_bytes()
                if hashlib.sha256(raw).hexdigest() != sample.get(field + '_sha256'):
                    raise ValueError('health_source_digest_changed')
                sources[field] = raw
                result[field] = {'path': 'health/' + name, 'sha256': hashlib.sha256(raw).hexdigest()}
                if tag == 'episode':
                    result[field].update(path='health/episode.snapshot.zip', member='checkpoint.bin' if field == 'checkpoint_file' else 'predictions.json')
            result['recovery_candidate'] = tag == 'healthy'
            # The pair commits as one bounded file. Flat files are compatibility
            # exports only; comparisons and qualification use this exact bundle.
            bundle = io.BytesIO()
            with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('checkpoint.bin', sources['checkpoint_file'])
                archive.writestr('predictions.json', sources['predictions_file'])
                archive.writestr('snapshot.json', json.dumps(result))
            raw = bundle.getvalue()
            if len(raw) > 8 * 1024 * 1024: raise ValueError('health_snapshot_bundle_oversized')
            self.write_bytes(tag + '.snapshot.zip', raw)
            result['bundle'] = {'path':'health/' + tag + '.snapshot.zip', 'sha256':hashlib.sha256(raw).hexdigest()}
            if tag in {'healthy', 'anomaly'}:
                for field in sources:
                    try: self.write_bytes(Path(result[field]['path']).name, sources[field])
                    except (OSError, ValueError) as error:
                        self.errors.append('compatibility_export_unavailable:' + str(error))
        except (OSError, ValueError) as error:
            result['recovery_candidate'] = False
            result['error'] = str(error); self.errors.append(str(error))
        return result

    def write_bytes(self, name, raw):
        path = self.root / 'health' / name
        tmp = path.with_suffix('.tmp')
        if path.is_symlink() or tmp.is_symlink(): raise ValueError('linked_health_output_rejected')
        tmp.write_bytes(raw)
        os.replace(tmp, path)

    def predictions(self, snapshot):
        return snapshot_predictions(self.root, snapshot, self.binding)

    def qualification(self):
        if not self.accepted: return 'evaluation_evidence_missing'
        if not self.updates: return 'optimizer_progress_missing'
        if not self.healthy or self.snapshot_error: return 'snapshot_evidence_incomplete'
        try:
            self.predictions(self.healthy)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            return 'snapshot_evidence_incomplete'
        return None

    def compare(self):
        try:
            before = self.predictions(self.healthy)
            after = self.predictions(self.episode)
            keys = ('sample_ids', 'targets')
            if any(before.get(k) != after.get(k) for k in keys) or before.get('protocol') != self.policy['protocol'] or after.get('protocol') != self.policy['protocol']:
                raise ValueError('health_prediction_comparison_identity_changed')
            arrays = [before['targets'], before['predictions'], after['predictions']]
            if not before.get('sample_ids') or len(before['sample_ids']) > 256 or any(not isinstance(a, list) or len(a) != len(before['sample_ids']) or any(not health_number(v) for v in a) for a in arrays):
                raise ValueError('bounded_finite_same_sample_predictions_required')
            self.write('comparison.json', {'binding': self.binding, 'protocol': self.policy['protocol'],
                'sample_ids': before['sample_ids'], 'targets': arrays[0], 'before': arrays[1], 'after': arrays[2],
                'delta': [b-a for a,b in zip(arrays[1], arrays[2])], 'root_cause_confirmed': False})
            low, high = min(min(a) for a in arrays), max(max(a) for a in arrays)
            span = max(high - low, 1e-9)
            lines = []
            for a, color in zip(arrays, ['black', 'blue', 'red']):
                pts = ' '.join('%.2f,%.2f' % (40+520*i/max(len(a)-1,1), 220-160*(v-low)/span) for i,v in enumerate(a))
                lines.append('<polyline fill="none" stroke="%s" points="%s"/>' % (color,pts))
            svg = '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="280"><rect width="600" height="280" fill="white"/><text x="25" y="25">Same samples: GT black / before blue / anomaly red</text><text x="25" y="265">Sample order; values %.6g to %.6g (shared linear scale)</text>%s</svg>' % (low,high,''.join(lines))
            (self.root / 'health/comparison.svg').write_text(svg, encoding='utf-8')
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
            self.errors.append('comparison_unavailable:' + str(error))

    def poll(self):
        try:
            path = self.root / 'health-sample.json'
            if path.resolve() != path or not path.is_file() or path.stat().st_size > 1024 * 1024:
                return None
            sample = json.loads(path.read_bytes())
            if not isinstance(sample, dict): raise ValueError('structured_health_sample_required')
            if sample.get('binding') != self.binding or type(sample.get('sequence')) is not int or sample['sequence'] <= self.sequence:
                return None
            self.sequence = sample['sequence']
            count = sample.get('optimizer_updates')
            if type(count) is not int or count < self.updates or type(sample.get('update_applied')) is not bool:
                raise ValueError('actual_optimizer_update_evidence_required')
            applied = sample['update_applied'] and count > self.updates
            if sample['update_applied'] and not applied: raise ValueError('claimed_update_did_not_advance')
            if count > self.updates: self.last_update_at = time.time()
            self.updates = count
            loss = sample.get('loss'); reason = None
            if applied:
                self.skips = 0
                self.bad_update = self.bad_update + 1 if loss is not None and not health_number(loss) else 0
            elif sample.get('amp_overflow') is True:
                self.skips += 1
                if self.skips >= self.policy['max_overflow_skips']: reason = 'overflow_without_optimizer_progress'
            if self.bad_update >= self.policy['bad_updates']: reason = 'nonfinite_actual_updates'
            valid = (sample.get('protocol') == self.policy['protocol'] and
                     type(sample.get('valid_samples')) is int and sample['valid_samples'] >= self.policy['minimum_samples'] and
                     sample.get('metric_name') == self.policy['metric'])
            value = sample.get('metric_value')
            bad = False
            if valid and value is not None:
                self.last_valid_at = time.time()
                bad = not health_number(value)
                if not bad:
                    p = self.policy
                    bad = ((value <= p['failure_boundary'] and p['baseline'] - value >= p['minimum_change']) if p['direction'] == 'maximize' else
                           (value >= p['failure_boundary'] and value - p['baseline'] >= p['minimum_change']))
                self.accepted += 1
            # Unknown or incompatible evaluations break the consecutive evidence chain.
            self.bad_eval = self.bad_eval + 1 if bad else 0
            if self.bad_eval >= self.policy['bad_evaluations']: reason = 'evaluation_collapse'
            if bad or self.bad_update or reason:
                if self.anomaly is None:
                    candidate = self.save_sources(sample, 'anomaly')
                    if 'error' not in candidate: self.anomaly = candidate
                if self.episode is None:
                    candidate = self.save_sources(sample, 'episode')
                    if 'error' not in candidate:
                        self.episode = candidate
                        if self.healthy: self.compare()
                    else: self.snapshot_error = candidate['error']
            if valid and health_number(value) and not bad and applied and health_number(loss):
                candidate = self.save_sources(sample, 'healthy')
                self.snapshot_error = candidate.get('error')
                if not self.snapshot_error:
                    self.healthy = candidate
                    self.episode = None
            status = {'binding': self.binding, 'sequence': self.sequence, 'optimizer_updates': count,
                      'bad_evaluations': self.bad_eval, 'bad_updates': self.bad_update, 'overflow_skips': self.skips,
                      'accepted_evaluations': self.accepted, 'last_healthy': self.healthy, 'first_anomaly': self.anomaly,
                      'current_episode':self.episode, 'snapshot_error':self.snapshot_error,
                      'errors': self.errors[-20:], 'stop_reason': reason, 'at': time.time(),
                      'evaluation': {'metric':self.policy['metric'],'protocol':sample.get('protocol'),
                          'value':value if health_number(value) else str(value)[:80],
                          'valid_samples':sample.get('valid_samples'),'comparable':valid},
                      'loss':loss if health_number(loss) else str(loss)[:80],
                      'update_applied':applied,'amp_overflow':sample.get('amp_overflow') is True}
            self.write('status.json', status)
            if self.records < 4096:
                with (self.root / 'health/history.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(status, allow_nan=False) + '\n')
                self.records += 1
            if reason: self.write('decision.json', status)
            return reason
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.errors.append(str(error))
            # Snapshot/storage failure never suppresses a decision already reached.
            return locals().get('reason')

    def check(self):
        reason = self.poll()
        if not reason and time.time() - self.last_valid_at >= self.policy['sample_timeout_seconds']:
            reason = 'health_observation_timeout'  # unavailable evidence, not proven divergence
        if not reason and 'progress_timeout_seconds' in self.policy and time.time() - self.last_update_at >= self.policy['progress_timeout_seconds']:
            reason = 'optimizer_progress_timeout'
        if reason:
            try: self.write('decision.json', {'binding':self.binding,'stop_reason':reason,'errors':self.errors[-20:]})
            except (OSError, ValueError): pass
        return reason
