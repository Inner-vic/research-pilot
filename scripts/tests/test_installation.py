"""Exercise installation boundaries without accounts, networks or training."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('installation_under_test',ROOT/'scripts/setup.py')
setup_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup_module)


class InstallationTests(unittest.TestCase):
    def test_inspect_reports_actual_entry_deployment_without_creating_a_study(self):
        module_path=ROOT/'skills/researchpilot/scripts/researchpilot.py'
        spec=importlib.util.spec_from_file_location('deployment_entry',module_path)
        entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
        before=list(self.project.rglob('*'))
        value=entry.inspect(self.project)
        self.assertEqual(value['deployment']['entry']['mode'],'source-checkout')
        self.assertEqual(value['deployment']['studies'],[])
        self.assertFalse(value['deployment']['execution_authority'])
        self.assertEqual(before,list(self.project.rglob('*')))

    def test_new_project_does_not_inherit_another_project_global_capability(self):
        module_path=ROOT/'skills/researchpilot/scripts/researchpilot.py'
        spec=importlib.util.spec_from_file_location('project_only_entry',module_path)
        entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
        global_skill=self.root/'global-skill';global_skill.mkdir()
        (global_skill/'CAPABILITIES.json').write_text('{"schema":"researchpilot-capabilities/v1","source":"another-project"}',encoding='utf-8')
        self.assertIsNone(entry.capability_profile(self.project,global_skill))
        current=self.project/'.bmad-ml/CAPABILITIES.json';current.parent.mkdir(exist_ok=True)
        current.write_text('{"schema":"researchpilot-capabilities/v1","source":"current-project"}',encoding='utf-8')
        self.assertEqual(entry.capability_profile(self.project,global_skill),str(current))

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rp-install-')
        self.root=Path(self.temp.name).resolve()
        self.project=self.root/'model';self.project.mkdir()

    def tearDown(self):
        # Extended paths are needed for cleanup on Windows as well as writes.
        if os.name=='nt':
            self.temp._finalizer.detach()
            import shutil
            storage=setup_module.load('test_install_storage',ROOT/'skills/sdm-module/sdm-lifecycle/scripts/review_storage.py')
            shutil.rmtree(storage.native_path(self.root))
        else:self.temp.cleanup()

    def sdk(self,version='0.163.0'):
        sdk=self.root/'sdk';(sdk/'openai_codex').mkdir(parents=True)
        (sdk/'openai_codex/client.py').write_text('# package-presence fixture; never imported\n')
        info=sdk/('openai_codex-'+version+'.dist-info');info.mkdir()
        (info/'METADATA').write_text('Metadata-Version: 2.1\nName: openai-codex\nVersion: '+version+'\n')
        return sdk

    def saved(self,sdk):
        folder=self.project/'.bmad-ml';folder.mkdir()
        path=folder/'ENVIRONMENT.json'
        value={'schema':'bmad-ml-environment/v1','python':sys.executable,
               'codex_sdk_root':str(sdk),'codex_sdk_version':'0.163.0',
               'model_status':'sdk-ready-auth-check-on-use'}
        path.write_text(json.dumps(value));return path

    def prepare(self,**extra):
        return setup_module.setup(project_root=self.project,skip_model_sdk=True,
                                  no_dashboard=True,no_install=True,**extra)

    def test_offline_check_preserves_configured_sdk_and_current_study(self):
        sdk=self.sdk();self.saved(sdk)
        current=self.project/'.bmad-ml/CURRENT.json'
        original=b'{"fixture":"an existing paused study"}\n';current.write_bytes(original)
        result=self.prepare()
        self.assertEqual(result['environment']['codex_sdk_root'],str(sdk))
        self.assertEqual(result['environment']['python'],sys.executable)
        self.assertEqual(result['environment']['model_status'],'sdk-ready-auth-check-on-use')
        self.assertEqual(current.read_bytes(),original)
        self.assertFalse(result['environment']['research_started'])
        self.assertEqual(result['environment']['platform_writes'],0)

    def test_invalid_saved_sdk_preserves_original_config_and_does_not_install(self):
        path=self.saved(self.root/'missing-sdk');original=path.read_bytes()
        destination=self.root/'user/researchpilot'
        with self.assertRaisesRegex(ValueError,'existing_official_codex_sdk_required'):
            setup_module.setup(project_root=self.project,skill_destination=destination,
                               skip_model_sdk=True,no_dashboard=True)
        self.assertEqual(path.read_bytes(),original)
        self.assertFalse(destination.exists())

    def test_fresh_offline_check_does_not_claim_model_readiness(self):
        result=self.prepare()
        self.assertIsNone(result['environment']['codex_sdk_root'])
        self.assertEqual(result['environment']['model_status'],'not-installed')
        self.assertEqual(result['environment']['model_calls'],0)

    def test_relative_parent_path_is_installed_and_reused(self):
        # This retains the original filesystem link/ownership checks.
        current=Path.cwd()
        working=self.root/'working';working.mkdir()
        try:
            os.chdir(working)
            first=setup_module.setup(project_root=self.project,skill_destination='../user/researchpilot',
                                     skip_model_sdk=True,no_dashboard=True)
            second=setup_module.setup(project_root=self.project,skill_destination='../user/researchpilot',
                                      skip_model_sdk=True,no_dashboard=True)
        finally:os.chdir(current)
        self.assertEqual(first['installation'],'installed')
        self.assertEqual(second['installation'],'already-installed')
        self.assertEqual(first['environment']['skill'],str(self.root/'user/researchpilot'))

    def test_deep_project_and_destination_build_verify_and_install(self):
        project=self.project/('deep-'*13)/('project-'*9);project.mkdir(parents=True)
        parent=self.root/('install-'*10)/('destination-'*3)
        destination=parent/('depth-'+('d'*max(1,190-len(str(parent)))))/'researchpilot'
        result=setup_module.setup(project_root=project,skill_destination=destination,
                                  skip_model_sdk=True,no_dashboard=True)
        installer=setup_module.load('test_final_installer',ROOT/'scripts/install-native-skill.py')
        self.assertEqual(installer.verify(destination),result['environment']['bundle_sha256'])
        bundle=json.loads((destination/'BUNDLE.json').read_text())
        self.assertGreater(max(len(str(destination/name)) for name in bundle['files']),260)
        self.assertFalse(str(result['environment']['skill']).startswith('\\\\?\\'))

    def legacy_bundle(self):
        builder=setup_module.load('migration_builder',ROOT/'scripts/build-native-skill.py')
        current=self.root/'bundle/researchpilot'
        builder.build(ROOT,current,'migration-test')
        legacy=self.root/'user/bmad-ml';legacy.parent.mkdir()
        import shutil
        shutil.copytree(current,legacy)
        entry=legacy/'scripts/researchpilot.py'
        text=entry.read_text(encoding='utf-8').replace('researchpilot-native-skill/v1','bmad-ml-native-skill/v1')
        text=text.replace("bundle.get('name')!='researchpilot'","bundle.get('name')!='bmad-ml'")
        text=text.replace('scripts/researchpilot.py','scripts/bmad_ml.py')
        entry.rename(legacy/'scripts/bmad_ml.py');(legacy/'scripts/bmad_ml.py').write_text(text,encoding='utf-8')
        inventory=json.loads((legacy/'BUNDLE.json').read_bytes())
        inventory.update(schema='bmad-ml-native-skill/v1',name='bmad-ml')
        inventory['files']={p.relative_to(legacy).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in legacy.rglob('*') if p.is_file() and p.name!='BUNDLE.json'}
        (legacy/'BUNDLE.json').write_text(json.dumps(inventory),encoding='utf-8')
        installer=setup_module.load('migration_installer',ROOT/'scripts/install-native-skill.py')
        installer.verify(legacy)
        return installer,current,legacy

    def test_owned_legacy_migrates_to_one_entry_and_preserves_study(self):
        installer,bundle,legacy=self.legacy_bundle()
        before={p.relative_to(legacy).as_posix():p.read_bytes() for p in legacy.rglob('*') if p.is_file()}
        state=self.project/'.bmad-ml/CURRENT.json';state.parent.mkdir();state.write_bytes(b'{"paused":true}\n')
        destination=legacy.with_name('researchpilot')
        result=setup_module.setup(project_root=self.project,skill_destination=destination,
                                  skip_model_sdk=True,no_dashboard=True)
        self.assertEqual(result['installation'],'installed')
        self.assertFalse(legacy.exists());self.assertTrue((destination/'SKILL.md').exists())
        self.assertEqual(state.read_bytes(),b'{"paused":true}\n')
        backups=list((self.project/'.runtime/skill-backups').glob('*/previous-legacy-skill'))
        self.assertEqual(len(backups),1)
        self.assertEqual(before,{p.relative_to(backups[0]).as_posix():p.read_bytes() for p in backups[0].rglob('*') if p.is_file()})
        self.assertEqual(installer.verify(destination),result['environment']['bundle_sha256'])
        again=setup_module.setup(project_root=self.project,skill_destination=destination,
                                 skip_model_sdk=True,no_dashboard=True)
        self.assertEqual(again['installation'],'already-installed')

    def test_unowned_extra_file_blocks_legacy_migration_without_overwrite(self):
        installer,bundle,legacy=self.legacy_bundle()
        extra=legacy/'keep-user-note.txt';extra.write_text('user-owned')
        with self.assertRaisesRegex(ValueError,'unexpected_native_bundle_files'):
            setup_module.setup(project_root=self.project,skill_destination=legacy.with_name('researchpilot'),
                               skip_model_sdk=True,no_dashboard=True)
        self.assertEqual(extra.read_text(),'user-owned')
        self.assertFalse(legacy.with_name('researchpilot').exists())

    def test_changed_legacy_digest_blocks_before_any_move(self):
        installer,bundle,legacy=self.legacy_bundle()
        before=(legacy/'BUNDLE.json').read_bytes();backup=self.root/'backup'
        with self.assertRaisesRegex(ValueError,'legacy_installed_digest_changed'):
            installer.install(bundle,legacy.with_name('researchpilot'),'absent',backup,legacy_digest='0'*64)
        self.assertEqual((legacy/'BUNDLE.json').read_bytes(),before)
        self.assertFalse(legacy.with_name('researchpilot').exists());self.assertFalse(backup.exists())

    def test_failed_new_readback_restores_exact_legacy_entry(self):
        installer,bundle,legacy=self.legacy_bundle()
        before=(legacy/'BUNDLE.json').read_bytes();destination=legacy.with_name('researchpilot')
        real_verify=installer.verify;digest=real_verify(legacy)
        def fail_readback(folder):
            if Path(folder).absolute()==destination and destination.exists():raise ValueError('fixture_readback_failure')
            return real_verify(folder)
        with patch.object(installer,'verify',side_effect=fail_readback):
            with self.assertRaisesRegex(ValueError,'fixture_readback_failure'):
                installer.install(bundle,destination,'absent',self.root/'backup',legacy_digest=digest)
        self.assertTrue(legacy.exists());self.assertFalse(destination.exists())
        self.assertEqual((legacy/'BUNDLE.json').read_bytes(),before);self.assertEqual(real_verify(legacy),digest)


if __name__=='__main__':unittest.main()
