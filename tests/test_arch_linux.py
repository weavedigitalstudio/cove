#!/usr/bin/env python3
"""Isolated installer regression tests; no root, network, or live config writes.

Run: python3 -m unittest discover -s tests -v
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(path, name):
    text = (ROOT / path).read_text()
    start = text.index(name + '() {')
    end = text.index('\n}', start) + 2
    return text[start:end] + '\n'


def bash(code, **env):
    return subprocess.run(['bash', '-c', code], text=True, capture_output=True,
                          env={**os.environ, **env})


class ArchLinux(unittest.TestCase):
    def test_distribution_detection_both_entrypoints(self):
        for path in ('main', 'install-cove.sh'):
            for distro, like, expected in (
                ('arch', '', 'pacman'), ('omarchy', 'arch', 'pacman'),
                ('manjaro', 'arch manjaro', 'pacman'), ('ubuntu', 'debian', 'apt'),
                ('debian', '', 'apt'), ('fedora', '', 'dnf'), ('rocky', 'rhel centos fedora', 'dnf'),
                ('unknown', 'archipelago', None),
            ):
                with self.subTest(path=path, distro=distro), tempfile.TemporaryDirectory() as tmp:
                    release = Path(tmp) / 'os-release'
                    release.write_text(f'ID={distro}\nID_LIKE="{like}"\n')
                    fn = function(path, 'setup_environment').replace('/etc/os-release', str(release))
                    result = bash('echo_info() { :; }; echo_success() { :; }; '
                                  'echo_error() { echo "$1" >&2; exit 1; }; '
                                  'uname() { echo Linux; }; id() { echo 1000; };\n' + fn +
                                  '\nsetup_environment\nprintf "%s" "$PKG_MANAGER"')
                    if expected:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout, expected)
                    else:
                        self.assertNotEqual(result.returncode, 0)

    def test_pacman_dependency_arguments(self):
        result = bash(function('commands/install', 'install_dependency') + '''
OS=linux PKG_MANAGER=pacman SUDO_CMD=''
gum() { :; }
pacman() { printf '%s\\n' "$*"; fake_arch_dependency() { :; }; }
install_dependency fake_arch_dependency brew-name apt-name dnf-name '' arch-name
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('-S --needed --noconfirm arch-name', result.stdout)
        self.assertNotIn('-Sy', result.stdout)

    def test_pacman_dependency_failure_is_reported(self):
        result = bash(function('commands/install', 'install_dependency') + '''
OS=linux PKG_MANAGER=pacman SUDO_CMD=''
gum() { :; }
pacman() { return 1; }
install_dependency absent_arch_dependency brew-name apt-name dnf-name '' arch-name
''')
        self.assertEqual(result.returncode, 1)

    def test_no_distro_php_extensions_on_arch(self):
        result = bash(function('main', 'linux_php_extension_packages') +
                      'PKG_MANAGER=pacman; linux_php_extension_packages')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_mariadb_initialization_guards(self):
        for state in ('missing', 'empty', 'initialized', 'unfamiliar'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                data = Path(tmp) / 'data'
                if state != 'missing':
                    data.mkdir()
                if state == 'initialized':
                    (data / 'mysql').mkdir()
                if state == 'unfamiliar':
                    (data / 'important.ibd').write_text('keep')
                result = bash(function('commands/install', 'initialize_arch_mariadb') + '''
SUDO_CMD=''
mariadbd() { printf 'datadir %s\\n' "$TEST_DATA"; }
mariadb-install-db() { printf 'INIT %s\\n' "$*"; }
initialize_arch_mariadb
''', TEST_DATA=str(data))
                if state == 'unfamiliar':
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn('INIT', result.stdout)
                    self.assertEqual((data / 'important.ibd').read_text(), 'keep')
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual('INIT' in result.stdout, state != 'initialized')
                    if state != 'initialized':
                        self.assertIn('--user=mysql --basedir=/usr --datadir=' + str(data), result.stdout)

    def test_mariadb_initialization_failure_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = bash(function('commands/install', 'initialize_arch_mariadb') + '''
SUDO_CMD=''
mariadbd() { printf 'datadir %s\\n' "$TEST_DATA"; }
mariadb-install-db() { return 7; }
initialize_arch_mariadb
''', TEST_DATA=str(Path(tmp) / 'new'))
            self.assertEqual(result.returncode, 7)

    def test_mariadb_rejects_unknown_datadir(self):
        result = bash(function('commands/install', 'initialize_arch_mariadb') + '''
SUDO_CMD=''
mariadbd() { echo 'datadir /'; }
mariadb-install-db() { echo UNEXPECTED; }
initialize_arch_mariadb
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('UNEXPECTED', result.stdout)

    def test_portable_php_uses_mariadb_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / 'php.ini'
            result = bash(function('commands/install', 'write_arch_php_socket') + '''
mariadbd() { printf '%s\n' '  socket' 'Variables (--variable-name=value)' 'socket /run/mysqld/mysqld.sock'; }
write_arch_php_socket
''', PHP_INI_FILE=str(ini))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('mysqli.default_socket = "/run/mysqld/mysqld.sock"', ini.read_text())
            self.assertIn('pdo_mysql.default_socket = "/run/mysqld/mysqld.sock"', ini.read_text())

    def test_socket_probe_against_installed_mariadb(self):
        if not Path('/usr/bin/mariadbd').is_file():
            self.skipTest('MariaDB is not installed')
        result = bash("mariadbd --verbose --help 2>/dev/null | awk '$1 == \"socket\" && $2 ~ /^\\// { print $2; exit }'")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip().startswith('/'), result.stdout)

    def test_mariadb_dropin_readable_with_restrictive_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = bash(function('main', 'write_mariadb_config') + '''
OS=linux IS_WSL=false SUDO_CMD=''
mariadb_config_dir() { printf '%s' "$TEST_CONFIG_DIR"; }
umask 077
write_mariadb_config || exit 9
chmod 600 "$TEST_CONFIG_DIR/cove.cnf"
write_mariadb_config
[ "$?" -eq 1 ] || exit 8
''', TEST_CONFIG_DIR=tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((Path(tmp) / 'cove.cnf').stat().st_mode & 0o777, 0o644)

    def test_local_transfer_uses_cove_php_without_changing_parent_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp) / 'bin'
            tools.mkdir()
            php = tools / 'php'
            php.write_text('#!/bin/bash\nprintf CovePHP\n')
            php.chmod(0o755)
            result = bash(function('commands/transfer', 'transfer_local') + '''
write_php_tools() { return 0; }
transfer_helper_source() { printf 'php; printf " args:%%s" "$*"\\n'; }
previous_path="$PATH"
transfer_local restore '/tmp/site with spaces' archive.zip
[ "$PATH" = "$previous_path" ]
''', COVE_TOOLS_DIR=str(tools))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('CovePHP args:restore /tmp/site with spaces archive.zip', result.stdout)

    def test_local_transfer_stops_if_php_wrapper_cannot_be_written(self):
        result = bash(function('commands/transfer', 'transfer_local') + '''
write_php_tools() { return 1; }
transfer_helper_source() { echo UNEXPECTED; }
transfer_local restore /tmp/site archive.zip
''')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('UNEXPECTED', result.stdout)

    def test_arch_trust_success_missing_root_and_failure(self):
        for state in ('present', 'absent', 'update-fails'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                cert = Path(tmp) / 'caddy/pki/authorities/local/root.crt'
                if state != 'absent':
                    cert.parent.mkdir(parents=True)
                    cert.write_text('test certificate')
                log = Path(tmp) / 'calls'
                result = bash(function('commands/trust', 'cove_trust') + '''
OS=linux PKG_MANAGER=pacman SUDO_CMD='' CADDY_CMD=frankenphp
install() { echo "install $*" >> "$TEST_LOG"; }
update-ca-trust() { echo update-ca-trust >> "$TEST_LOG"; [ "$TEST_STATE" != update-fails ]; }
# All browser and filesystem calls are mocked, so no real profile is touched.
certutil() { :; }
mkdir() { :; }
find() { [ "$1" = "$XDG_DATA_HOME/caddy/pki/authorities/local" ] && printf '%s/root.crt\\n' "$1"; }
openssl() { :; }
gum() { :; }
frankenphp() { echo UNEXPECTED; return 9; }
cove_trust
''', XDG_DATA_HOME=tmp, TEST_LOG=str(log), TEST_STATE=state)
                self.assertEqual(result.returncode == 0, state == 'present', result.stderr)
                self.assertNotIn('UNEXPECTED', result.stdout)
                if state == 'absent':
                    self.assertFalse(log.exists())
                else:
                    self.assertIn('/etc/ca-certificates/trust-source/anchors/cove-local-authority.crt', log.read_text())
                    self.assertIn('update-ca-trust', log.read_text())

    def test_arch_untrust_removes_only_cove_anchor(self):
        result = bash(function('commands/uninstall', 'cove_untrust_root') + '''
OS=linux PKG_MANAGER=pacman SUDO_CMD=''
rm() { printf 'REMOVE %s\\n' "$*"; }
update-ca-trust() { echo REFRESH; }
find() { :; }
certutil() { :; }
cove_untrust_root
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('REMOVE -f /etc/ca-certificates/trust-source/anchors/cove-local-authority.crt', result.stdout)
        self.assertIn('REFRESH', result.stdout)


if __name__ == '__main__':
    unittest.main()
