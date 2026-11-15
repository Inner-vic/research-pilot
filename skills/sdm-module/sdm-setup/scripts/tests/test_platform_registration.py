"""Registration checks must match the engine's actual byte-binding algorithm."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

SETUP_SCRIPTS = Path(__file__).resolve().parents[1]
MODULE_ROOT = SETUP_SCRIPTS.parents[1]
sys.path.insert(0, str(MODULE_ROOT / 'sdm-lifecycle/scripts'))
from specctl import _installed_skill_tree_digest

spec = importlib.util.spec_from_file_location('binding_check', SETUP_SCRIPTS / 'check_platform_bindings.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class RegistrationTests(unittest.TestCase):
    def test_digest_matches_engine_and_changes_with_script_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / '.agents/skills/example-platform'
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text('example', encoding='utf-8')
            (skill / 'script.py').write_text('print(1)', encoding='utf-8')
            before = checker.inspect(root, 'example-platform')['skill_tree_digest']
            self.assertEqual(before, _installed_skill_tree_digest(skill))
            (skill / 'script.py').write_text('print(2)', encoding='utf-8')
            after = checker.inspect(root, 'example-platform')['skill_tree_digest']
            self.assertNotEqual(before, after)
            self.assertEqual(after, _installed_skill_tree_digest(skill))

    def test_rejects_escape_and_incomplete_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(checker.inspect(root, '../escape')['registration_ready'])
            (root / '.agents/skills/incomplete').mkdir(parents=True)
            self.assertEqual(checker.inspect(root, 'incomplete')['error'], 'skill_incomplete')

    def test_rejects_linked_script(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / '.agents/skills/example-platform'
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text('example')
            (skill / 'linked.py').symlink_to(skill / 'SKILL.md')
            self.assertEqual(checker.inspect(root, 'example-platform')['error'], 'unsafe_skill_node')


if __name__ == '__main__':
    unittest.main()
