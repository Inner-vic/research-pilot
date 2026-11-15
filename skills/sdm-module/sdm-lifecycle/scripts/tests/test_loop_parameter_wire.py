import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SCRIPTS))
from prepare_execution_continuation import loop_parameter_digest
from canonical import digest

class ParameterWireTest(unittest.TestCase):
    def test_platform_and_continuation_agree_on_small_learning_rates(self):
        root=SCRIPTS.parents[3]
        path=root/'.agents/skills/lpai-s12-platform/scripts/prepare_roadmodel_minimal.py'
        spec=importlib.util.spec_from_file_location('loop_preparation_wire',path)
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        for controls in ({'amp':True,'learning_rate':4e-5},{'amp':False,'learning_rate':4e-5},{'amp':False,'learning_rate':2e-5}):
            self.assertEqual(loop_parameter_digest(controls),module.value_digest(controls))
        self.assertNotEqual(loop_parameter_digest({'amp':True,'learning_rate':4e-5}),digest({'amp':True,'learning_rate':4e-5}))

if __name__=='__main__': unittest.main()
