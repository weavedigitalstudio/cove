"""Opt-in local listeners coexist with HTTPS on another network interface."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_arch_linux import function,bash

class LoopbackListeners(unittest.TestCase):
    def emit(self,enabled,port):
        # Production commands read just selected settings at startup. Do not
        # source the config or export the flag: exercise the actual file lookup.
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config'
            config.write_text("COVE_LOOPBACK_ONLY='"+enabled+"'\n")
            return bash(function('main','config_get')+
                        function('main','cove_caddy_loopback_bind')+
                        function('main','cove_caddy_loopback_http_redirect')+'''
https_port_suffix() { [ "$HTTPS_PORT" = 443 ] || printf ':%s' "$HTTPS_PORT"; }
cove_caddy_loopback_bind
printf '\\n--HTTP--\\n'
cove_caddy_loopback_http_redirect
''',CONFIG_FILE=str(config),COVE_LOOPBACK_ONLY='false',HTTPS_PORT=str(port))

    def test_default_has_no_bind_or_redirect_override(self):
        for value in ('false','','bogus'):
            result=self.emit(value,443)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout,'\n--HTTP--\n')

    def test_explicit_http_preserves_the_https_port(self):
        for port,suffix in ((443,''),(8453,':8453')):
            result=self.emit('true',port)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('default_bind 127.0.0.1 ::1',result.stdout)
            self.assertIn('redir https://{host}'+suffix+'{uri} permanent',result.stdout)

    def test_caddy_adapts_to_only_loopback_http_and_https_listeners(self):
        binary=shutil.which('frankenphp')
        if not binary:self.skipTest('FrankenPHP not installed')
        bind,redirect=self.emit('true',443).stdout.split('--HTTP--\n')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'Caddyfile'
            path.write_text('{\n'+bind+'}\nhttps://cove.localhost {\n tls internal\n respond "fixture"\n}\n'+redirect)
            result=subprocess.run([binary,'adapt','--config',str(path),'--adapter','caddyfile'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            config=json.loads(result.stdout)
            listeners={address for server in config['apps']['http']['servers'].values() for address in server['listen']}
            self.assertEqual(listeners,{'127.0.0.1:80','[::1]:80','127.0.0.1:443','[::1]:443'})
