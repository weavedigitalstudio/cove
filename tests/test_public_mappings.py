"""Public aliases remain in Caddy mappings without shadowing external DNS."""
from pathlib import Path
import tempfile
import unittest
from test_arch_linux import function, bash


class PublicMappings(unittest.TestCase):
    def run_mapping(self, tmp, command):
        code = function('commands/mappings', 'cove_mappings') + '''
SITES_DIR="$TEST_DIR"
gum() { :; }
site_not_found() { exit 10; }
validate_hostname() { [[ "$1" != *' '* ]]; }
cove_domain_owner() { return 1; }
regenerate_caddyfile() { echo CADDY; }
update_etc_hosts() { echo HOSTS; }
prune_etc_hosts() {
    if grep -Fxq -- "$1" "$SITES_DIR/pilot.localhost/public_mappings"; then
        echo "PRUNE $1"
    else
        echo "PRUNE_REMOVED $1"
    fi
}
''' + command
        return bash(code, TEST_DIR=tmp)

    def test_public_add_and_conversion_are_idempotent(self):
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as tmp:
                site = Path(tmp, 'pilot.localhost'); site.mkdir()
                if existing:
                    (site / 'mappings').write_text('pilot.example.com\n')
                result = self.run_mapping(tmp, 'cove_mappings pilot add pilot.example.com --public\ncove_mappings pilot add pilot.example.com --public')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((site / 'mappings').read_text(), 'pilot.example.com\n')
                self.assertEqual((site / 'public_mappings').read_text(), 'pilot.example.com\n')
                self.assertEqual(result.stdout.count('PRUNE pilot.example.com'), 2)

    def test_required_hosts_excludes_public_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp, 'pilot.localhost'); (site / 'public').mkdir(parents=True)
            (site / 'mappings').write_text('local.test\npilot.example.com\n*.wild.test\n')
            (site / 'public_mappings').write_text('pilot.example.com\n')
            result = bash(function('main', 'cove_required_hosts') + '''
SITES_DIR="$TEST_DIR" CUSTOM_CADDY_DIR="$TEST_DIR/custom"
validate_hostname() { return 0; }
site_twin() { return 1; }
cove_required_hosts
cove_required_hosts
''', TEST_DIR=tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count('local.test\n'), 2)
            self.assertIn('pilot.localhost\n', result.stdout)
            self.assertNotIn('pilot.example.com', result.stdout)
            self.assertNotIn('*.wild', result.stdout)

    def test_remove_cleans_both_alias_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp, 'pilot.localhost'); site.mkdir()
            for name in ('mappings', 'public_mappings'):
                (site / name).write_text('pilot.example.com\nkeep.example.com\n')
            result = self.run_mapping(tmp, 'cove_mappings pilot remove pilot.example.com')
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ('mappings', 'public_mappings'):
                self.assertEqual((site / name).read_text(), 'keep.example.com\n')
            self.assertIn('PRUNE_REMOVED pilot.example.com', result.stdout)

    def test_unknown_flag_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp, 'pilot.localhost'); site.mkdir()
            result = self.run_mapping(tmp, 'cove_mappings pilot add pilot.example.com --typo')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(site.iterdir()), [])
