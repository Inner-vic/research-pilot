"""Entry drift is diagnostic; it never migrates an original frozen study."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research_deployment import inspect


class DeploymentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.project=self.root/'project';self.project.mkdir()
  self.skill=self.root/'skills/researchpilot';self.skill.mkdir(parents=True)
  self.runtime=self.root/'entry';self.runtime.mkdir()
  self.frozen=self.project/'frozen';self.frozen.mkdir()
  self.write(self.skill/'BUNDLE.json',{'version':'0.3.1'})
  (self.runtime/'research_launch.py').write_text('new entry')
  (self.frozen/'research_launch.py').write_text('original freeze')
  self.config=self.project/'service.json'
  self.write(self.config,{'project_id':'study','runtime_root':str(self.frozen),'runtime_manifest':{
   'research_launch.py':hashlib.sha256(b'original freeze').hexdigest()}})
  self.index=self.project/'.bmad-ml/CURRENT.json'
  self.write(self.index,{'schema':'bmad-ml-active-study/v1','study_id':'study','config':str(self.config)})
 def tearDown(self):self.tmp.cleanup()
 def write(self,path,value):
  path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf-8')
 def view(self):return inspect(self.project,self.skill,self.runtime,[self.index],'native-skill')

 def test_older_unchanged_freeze_is_retained_not_migrated(self):
  before={str(p):p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
  value=self.view();row=value['studies'][0]
  self.assertEqual(value['entry']['version'],'0.3.1')
  self.assertEqual(row['status'],'original-frozen-runtime')
  self.assertEqual(row['differs_from_current_entry'],['research_launch.py'])
  self.assertEqual(row['changed_frozen_files'],[]);self.assertFalse(row['automatic_migration'])
  self.assertEqual(before,{str(p):p.read_bytes() for p in self.project.rglob('*') if p.is_file()})

 def test_changed_original_file_is_distinct_from_an_older_version(self):
  (self.frozen/'research_launch.py').write_text('tampered')
  row=self.view()['studies'][0]
  self.assertEqual(row['status'],'integrity-mismatch')
  self.assertEqual(row['changed_frozen_files'],['research_launch.py'])

 def test_external_config_is_not_read(self):
  self.write(self.index,{'schema':'bmad-ml-active-study/v1','study_id':'external',
   'config':str(self.root/'nonexistent-private-config.json')})
  self.assertEqual(self.view()['studies'][0]['status'],'outside-current-project')


if __name__=='__main__':unittest.main()
