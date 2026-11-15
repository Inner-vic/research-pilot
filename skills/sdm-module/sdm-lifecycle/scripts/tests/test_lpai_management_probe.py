"""Counterexamples for capability and binding inference, without platform writes."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lpai_management_probe as probe


class ManagementProbeTests(unittest.TestCase):
    def profile(self, email='intern@example.com'):
        return {'ok': True, 'identity': 'user', 'data': {'users': [
            {'email': email, 'enterprise_email': email, 'open_id': 'ou_current', 'is_activated': True}]}}

    def test_pending_with_email_mismatch_is_suspicion_not_proven_cause(self):
        value = probe.binding_diagnosis({'status': 'pending_approval', 'approval_id': 'pending-1',
                                         'recipients': ['owner@example.com']}, self.profile())
        self.assertEqual(value['binding_observation'], 'address-mismatch-alias-mapping-unverified')
        self.assertFalse(value['root_cause_proven'])
        self.assertFalse(value['pending_is_submitted'])

    def test_matching_email_does_not_prove_delivery(self):
        value = probe.binding_diagnosis({'recipients': [' Intern@Example.COM ']}, self.profile())
        self.assertEqual(value['binding_observation'], 'address-match-delivery-unverified')
        self.assertFalse(value['delivery_verified'])

    def test_bot_empty_and_multiple_users_cannot_be_current_user(self):
        profile = self.profile()
        profile['identity'] = 'bot'
        self.assertEqual(probe.binding_diagnosis({'recipients': ['x']}, profile)['binding_observation'], 'unresolved')
        profile['identity'] = 'user'
        profile['data']['users'] *= 2
        self.assertEqual(probe.binding_diagnosis({'recipients': ['x']}, profile)['binding_observation'], 'unresolved')

    def test_sandbox_or_auth_read_failure_does_not_mean_unbound(self):
        value = probe.binding_diagnosis({'recipients': ['owner@example.com']}, {'ok': False, 'error': {'type': 'network'}})
        self.assertEqual(value['binding_observation'], 'unresolved')

    def test_private_receipt_unwrap_drops_unrelated_raw_data(self):
        value = probe.binding_diagnosis({'stdout': '{"status":"pending_approval","recipients":["intern@example.com"]}',
                                         'stderr': 'secret-do-not-save'}, self.profile())
        self.assertNotIn('secret-do-not-save', str(value))

    def test_capabilities_never_run_mutations_or_claim_live_write(self):
        calls = []
        def fake_run(argv):
            calls.append(argv)
            return 0, None, 'Available Commands:\n  get      Read\n  start    Start\n\nFlags:\n'
        with patch.object(probe, 'run', side_effect=fake_run), patch.object(probe, 'sdk_methods', return_value=({'project.py:Project': ['create', 'update']}, [])):
            value = probe.capability_report('official-cli', Path('unused'))
        self.assertTrue(all(argv[-1] == '--help' for argv in calls))
        rows = {row['operation']: row for row in value['operations']}
        self.assertTrue(rows['component.edit']['sdk_implemented'])
        self.assertFalse(rows['devspace.create']['cli_implemented'])
        self.assertFalse(any(row['live_write_verified'] for row in value['operations']))

    def test_missing_sdk_is_not_reported_as_missing_operations(self):
        with self.assertRaisesRegex(ValueError, 'installed_sdk_package_directory_required'):
            probe.sdk_methods(Path(__file__) / 'not-a-package')

    def test_conflicting_diagnostic_accounts_cannot_identify_binding(self):
        responses = [(0, {'identities': {'user': {'openId': 'ou_other'}}}, ''),
                     (0, self.profile(), ''), (1, None, '')]
        with patch.object(probe, 'run', side_effect=responses), patch.object(Path, 'read_bytes', return_value=b'{"recipients":["owner@example.com"]}'):
            value = probe.doctor('cli', 'lark', 'ns', 'space', 'receipt')
        self.assertFalse(value['identity_consistent'])
        self.assertEqual(value['binding_observation'], 'unresolved-inconsistent-diagnostic-identities')

    def test_doctor_has_only_three_fixed_reads_and_suppresses_auth_secrets(self):
        responses = [(0, {'appId': 'diagnostic-app', 'token': 'secret', 'identities': {'user': {'verified': True}}}, ''),
                     (0, self.profile(), ''),
                     (0, {'data': {'metadata': {'name': 'space', 'namespace': 'ns', 'annotations': {
                         'lpai.org/owner': 'owner@example.com', 'lpai.org/res-stat-task-origin-owner': 'intern@example.com'}}}}, '')]
        with patch.object(probe, 'run', side_effect=responses) as run, patch.object(Path, 'read_bytes', return_value=b'{"status":"pending_approval","recipients":["owner@example.com"]}'):
            value = probe.doctor('cli', 'lark', 'ns', 'space', 'receipt')
        self.assertEqual(run.call_count, 3)
        self.assertFalse(any(set(call.args[0]) & {'create', 'start', 'stop', 'send', 'login'} for call in run.call_args_list))
        self.assertNotIn('secret', str(value))
        self.assertEqual(value['platform_original_owner'], 'intern@example.com')


if __name__ == '__main__':
    unittest.main()
