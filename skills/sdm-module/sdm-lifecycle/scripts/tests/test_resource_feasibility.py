import copy
import datetime as dt
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resource_feasibility import assess, request_digest, summarize


class ResourceFeasibilityTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc)
        self.request = {'provider': 'fixture-component', 'namespace': 'fixture', 'cluster': 'fixture-cluster',
                        'queue_id': 'fixture-queue', 'scheduling_mode': 'guaranteed',
                        'roles': [{'name': 'master', 'instances': 1, 'cpu_cores_per_instance': 104,
                                   'memory_gib_per_instance': 872, 'gpus_per_instance': 8, 'gpu_type': 'GPU-A'}]}

    def observation(self, scope, **fields):
        return {'scope': scope, 'status': 'observed', 'request_digest': request_digest(self.request),
                'observed_at': (self.now - dt.timedelta(seconds=10)).isoformat(),
                'valid_until': (self.now + dt.timedelta(seconds=30)).isoformat(),
                'source_ref': 'cpu-fixture:' + scope, **fields}

    def headroom(self):
        return {'cpu_cores': 3344, 'memory_gib': 27968, 'gpus_by_type': {'GPU-A': 256}}

    def snapshots(self):
        return [self.observation('queue_headroom', available=self.headroom()),
                self.observation('space_headroom', available=self.headroom()),
                self.observation('catalog', supported=True),
                self.observation('joint_placement', fits_full_request=True)]

    def test_aggregate_free_cards_cannot_establish_single_node_fit(self):
        evidence = self.snapshots()[:-1]
        result = assess(self.request, evidence, now=self.now)
        self.assertEqual(result['assessment'], 'unknown')
        self.assertFalse(result['execution_authorized'])

    def test_cpu_memory_and_gpu_shortages_are_independent(self):
        for dimension, value in [('cpu_cores', 100), ('memory_gib', 800), ('gpus_by_type', {'GPU-A': 0})]:
            with self.subTest(dimension=dimension):
                evidence = self.snapshots()
                evidence[0]['available'][dimension] = value
                result = assess(self.request, evidence, now=self.now)
                self.assertEqual(result['assessment'], 'known-constraint')
                failed = [c for c in result['checks'] if c['status'] == 'does-not-fit']
                self.assertEqual(len(failed), 1)
                self.assertEqual(failed[0]['scope'], 'queue_headroom')

    def test_full_fresh_fit_is_neither_reservation_nor_execution_permission(self):
        result = assess(self.request, self.snapshots(), now=self.now)
        self.assertEqual(result['assessment'], 'fits-observed-snapshot')
        self.assertFalse(result['reservation_verified'])
        self.assertFalse(result['historical_root_cause_established'])
        self.assertFalse(result['execution_authorized'])

    def test_documented_combined_headroom_can_replace_two_quota_views_not_placement(self):
        evidence = [self.observation('effective_headroom', available=self.headroom()),
                    self.observation('catalog', supported=True)]
        self.assertEqual(assess(self.request, evidence, now=self.now)['assessment'], 'unknown')
        evidence.append(self.observation('joint_placement', fits_full_request=True))
        self.assertEqual(assess(self.request, evidence, now=self.now)['assessment'], 'fits-observed-snapshot')
        evidence.append(self.observation('queue_headroom', available=self.headroom()))
        self.assertEqual(assess(self.request, evidence, now=self.now)['assessment'], 'unknown')

    def test_current_snapshot_does_not_match_old_or_changed_request(self):
        evidence = self.snapshots()
        for field, value in [('cpu_cores_per_instance', 48), ('memory_gib_per_instance', 256), ('gpu_type', 'GPU-B')]:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.request)
                changed['roles'][0][field] = value
                result = assess(changed, evidence, now=self.now)
                self.assertEqual(result['assessment'], 'unknown')
                self.assertTrue(all(r['reason'] == 'different_resource_envelope' for r in result['ignored']))

    def test_stale_future_and_duplicate_evidence_remain_unknown(self):
        for kind in ['stale', 'future', 'duplicate']:
            with self.subTest(kind=kind):
                evidence = self.snapshots()
                if kind == 'stale':
                    evidence[0]['observed_at'] = (self.now - dt.timedelta(minutes=6)).isoformat()
                elif kind == 'future':
                    evidence[0]['observed_at'] = (self.now + dt.timedelta(seconds=5)).isoformat()
                else:
                    evidence.append(copy.deepcopy(evidence[0]))
                self.assertEqual(assess(self.request, evidence, now=self.now)['assessment'], 'unknown')

    def test_joint_placement_failure_blocks_aggregate_fit(self):
        evidence = self.snapshots()
        evidence[-1]['fits_full_request'] = False
        self.assertEqual(assess(self.request, evidence, now=self.now)['assessment'], 'known-constraint')

    def test_all_roles_and_gpu_types_are_summed_without_package_double_counting(self):
        self.request['roles'].append({'name': 'worker', 'instances': 2, 'cpu_cores_per_instance': 16,
                                     'memory_gib_per_instance': 64, 'gpus_per_instance': 4, 'gpu_type': 'GPU-B',
                                     'packages_per_instance': 1, 'program_processes': 1})
        totals = summarize(self.request)
        self.assertEqual(totals, {'cpu_cores': 136, 'memory_gib': 1000, 'instances': 3,
                                 'gpus_by_type': {'GPU-A': 8, 'GPU-B': 8}})

    def test_wrong_gpu_type_or_unknown_numeric_dimensions_cannot_pass(self):
        evidence = self.snapshots()
        evidence[0]['available']['gpus_by_type'] = {'GPU-B': 256}
        self.assertEqual(assess(self.request, evidence, now=self.now)['assessment'], 'unknown')
        for value in [float('nan'), float('inf'), True, -1]:
            with self.subTest(value=value):
                bad = copy.deepcopy(self.request)
                bad['roles'][0]['cpu_cores_per_instance'] = value
                with self.assertRaises(ValueError):
                    summarize(bad)


if __name__ == '__main__':
    unittest.main()
