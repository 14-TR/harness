import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

from harness import research


class ResearchCLITests(unittest.TestCase):
    def test_public_commands_are_opt_in_and_preview_never_initializes_git(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            config = root / 'config.json'
            config.write_text(json.dumps({'data_dir': str(root / 'state')}))
            argv = [sys.executable, '-m', 'harness.research', '--config', str(config)]
            status = subprocess.run(argv + ['public-status'], capture_output=True, text=True, timeout=10)
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout), [])
            retry = subprocess.run(argv + ['public-retry'], capture_output=True, text=True, timeout=10)
            self.assertEqual(retry.returncode, 1)
            self.assertIn('public_command_failed', retry.stderr)
            preview = subprocess.run(argv + ['public-prepare', '--output', str(root / 'preview')], capture_output=True, text=True, timeout=10)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertFalse(json.loads(preview.stdout)['uploaded'])
            self.assertFalse(list((root / 'preview').rglob('.git')))
            self.assertFalse((root / 'state').exists())

    def test_status_and_launchd_generation_do_not_run_or_install(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            config = root / 'config.json'
            config.write_text(json.dumps({'data_dir': str(root / 'state')}))
            command = [sys.executable, '-m', 'harness.research', '--config', str(config)]
            status = subprocess.run(command + ['status'], capture_output=True, text=True)
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout), [])
            target = root / 'daily.plist'
            result = subprocess.run(command + ['launchd', '--output', str(target)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            plist = plistlib.loads(target.read_bytes())
            self.assertEqual(plist['StartCalendarInterval'], {'Hour': 8, 'Minute': 15})
            self.assertEqual(plist['ProgramArguments'][-1], 'run')
            self.assertIn(str(config), plist['ProgramArguments'])
            self.assertFalse(plist['RunAtLoad'])
            self.assertFalse(plist['KeepAlive'])
            self.assertFalse((root / 'state' / 'research.sqlite3').exists())
            invalid = subprocess.run(command + ['launchd', '--output', str(root / 'bad.plist'), '--hour', '24'], capture_output=True, text=True)
            self.assertNotEqual(invalid.returncode, 0)

    def test_config_rejects_unknown_keys_and_nonlocal_model(self):
        with self.assertRaises(ValueError):
            research.Config(host='http://192.168.1.1:11434').validate()
        with self.assertRaises(ValueError):
            research.Config(count=True).validate()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.json'
            path.write_text('{"coutn":5}')
            with self.assertRaises(ValueError):
                research.load_config(path)


if __name__ == '__main__':
    unittest.main()
