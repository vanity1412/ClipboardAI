"""Offline network regressions: all adapters, WLAN and HTTPS calls are fake."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import network_switch as switch

WIFI = '11111111-1111-1111-1111-111111111111'


class ShutdownTests(unittest.TestCase):
    def test_aborted_exit_allows_new_switch_after_recovery_has_finished(self):
        with patch.object(switch, '_network_closing', threading.Event()), patch.object(switch, '_execute_switch', return_value=dict(ok=True)) as helper:
            self.assertTrue(switch.shutdown_network(timeout=0))
            self.assertFalse(switch.run_switch('lan', 'no.real.network')['ok'])
            helper.assert_not_called()
            switch.abort_network_shutdown()
            self.assertTrue(switch.run_switch('lan', 'no.real.network')['ok'])
            helper.assert_called_once()

    def test_thread_creation_failure_releases_pending_state_and_resources(self):
        created = []
        temporary_directory = tempfile.TemporaryDirectory

        def folder(*args, **kwargs):
            value = temporary_directory(*args, **kwargs)
            created.append(Path(value.name))
            return value

        with patch.object(switch, '_network_closing', threading.Event()), patch.object(switch.tempfile, 'TemporaryDirectory', side_effect=folder), patch.object(switch.threading, 'Thread', side_effect=RuntimeError('synthetic thread failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic thread failure'):
                switch.run_switch('lan', 'no.real.network')
        self.assertFalse(switch.network_recovery_pending())
        self.assertIsNone(switch._active_switch)
        self.assertFalse(any(path.exists() for path in created))
        self.assertTrue(switch._switch_lock.acquire(blocking=False))
        switch._switch_lock.release()

    def test_shutdown_keeps_transaction_resources_until_recovery_finishes(self):
        entered, release, recovered = threading.Event(), threading.Event(), threading.Event()
        paths = {}

        def helper(*args):
            paths['result'], paths['cancel'] = args[-2:]
            entered.set()
            release.wait(3)
            self.assertTrue(paths['cancel'].exists())
            recovered.set()
            return dict(ok=False, code='switch_timeout')

        with patch.object(switch, '_network_closing', threading.Event()), patch.object(switch, '_execute_switch', side_effect=helper):
            caller = threading.Thread(target=lambda: switch.run_switch('wifi', 'no.real.network'))
            caller.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertFalse(switch.shutdown_network(timeout=.01))
                self.assertTrue(paths['cancel'].exists())
                self.assertTrue(paths['result'].exists())
                self.assertTrue(switch.network_recovery_pending())
                release.set()
                self.assertTrue(switch.shutdown_network(timeout=2))
                self.assertTrue(recovered.is_set())
                self.assertFalse(paths['result'].exists())
                self.assertFalse(switch.network_recovery_pending())
                self.assertEqual(switch.run_switch('lan', 'no.real.network')['code'], 'switch_timeout')
            finally:
                release.set()
                caller.join(3)
                self.assertFalse(caller.is_alive())

    def test_cancel_before_launch_does_not_request_elevation(self):
        with tempfile.TemporaryDirectory() as folder:
            result, cancel = Path(folder) / 'result.json', Path(folder) / 'cancel'
            cancel.write_text('cancel')
            with patch.object(switch, '_launch_helper') as launch:
                response = switch._execute_switch('wifi', 'no.real.network', False,
                    WIFI, None, None, result, cancel)
            self.assertEqual(response['code'], 'switch_timeout')
            launch.assert_not_called()

    def test_proxy_selection_and_bypass_follow_calling_user(self):
        def request(script):
            import base64
            payload = script.split("FromBase64String('", 1)[1].split("')", 1)[0]
            return json.loads(base64.b64decode(payload))

        with patch.object(switch, 'getproxies', return_value={'https': 'http://proxy.example:3128'}), patch.object(switch, 'proxy_bypass', return_value=False):
            data = request(switch.make_switch_script('lan', 'api.example', Path('result.json')))
            self.assertEqual(data['probe_proxy'], 'http://proxy.example:3128')
            self.assertEqual(data['wifi_helper'], switch.WLAN_HELPER_SOURCE)
        with patch.object(switch, 'getproxies', return_value={'https': 'http://proxy.example:3128'}), patch.object(switch, 'proxy_bypass', return_value=True):
            self.assertNotIn('probe_proxy', request(switch.make_switch_script('lan', 'api.example', Path('result.json'))))

    def test_inventory_command_stays_below_windows_command_limit(self):
        rows = [dict(id=WIFI, kind='wifi', enabled=True)]
        response = subprocess.CompletedProcess([], 0, json.dumps(rows).encode(), b'')
        with patch.object(switch.subprocess, 'run', return_value=response) as run:
            switch.read_adapters()
        self.assertLess(len(subprocess.list2cmdline(run.call_args.args[0])), 32767)


@unittest.skipUnless(os.name == 'nt', 'Windows script sharing and PowerShell mocks')
class WindowsRecoveryTests(unittest.TestCase):
    def test_helper_exception_releases_script_read_lock(self):
        with tempfile.TemporaryDirectory(prefix='ClipboardAI_HelperFailure_') as folder:
            path = Path(folder) / 'helper.ps1'
            with self.assertRaisesRegex(RuntimeError, 'synthetic helper failure'):
                with switch._locked_script(path, "Write-Output 'safe-offline-test'\n"):
                    raise RuntimeError('synthetic helper failure')
            path.unlink()

    def test_script_changed_before_read_lock_is_rejected_and_handle_is_closed(self):
        original_open = Path.open

        class TamperedWriter:
            def __init__(self, path, file):
                self.path, self.file = path, file

            def __enter__(self):
                return self.file

            def __exit__(self, *args):
                self.file.close()
                with original_open(self.path, 'wb') as output:
                    output.write(b'changed-before-lock')

        def open_file(path, mode='r', *args, **kwargs):
            file = original_open(path, mode, *args, **kwargs)
            return TamperedWriter(path, file) if mode == 'xb' else file

        with tempfile.TemporaryDirectory(prefix='ClipboardAI_TamperTest_') as folder:
            path = Path(folder) / 'helper.ps1'
            with patch.object(Path, 'open', open_file):
                with self.assertRaisesRegex(OSError, 'switch_failed'):
                    with switch._locked_script(path, "Write-Output 'original'\n"):
                        self.fail('Changed helper must not be launched')
            path.write_text('read lock has been released')
            path.unlink()

    def test_https_probe_uses_proxy_has_deadline_and_disposes_response(self):
        simulation = r'''
$global:disposed=$false
$global:response=[pscustomobject]@{StatusCode=200}
$global:response | Add-Member ScriptMethod Dispose {$global:disposed=$true}
$global:probe=[pscustomobject]@{Method='';Timeout=0;ReadWriteTimeout=0;AllowAutoRedirect=$true;Proxy=$null}
$global:probe | Add-Member ScriptMethod GetResponse {return $global:response}
function New-ProviderProbe($hostName) {return $global:probe}
$script:ProbeProxy='http://proxy.example:3128'
$ok=Test-ProviderConnection 'no.real.network'
$result=@{ok=$ok;method=$global:probe.Method;timeout=$global:probe.Timeout;redirects=$global:probe.AllowAutoRedirect;proxy=$global:probe.Proxy.Address.AbsoluteUri;disposed=$global:disposed}
$global:probe | Add-Member ScriptMethod GetResponse {throw 'synthetic TLS or network failure'} -Force
$result.failure=Test-ProviderConnection 'no.real.network'
ConvertTo-Json $result -Compress
'''
        with tempfile.TemporaryDirectory(prefix='ClipboardAI_ProbeTest_') as folder:
            path = Path(folder) / 'mock.ps1'
            path.write_text(switch.POWERSHELL_FUNCTIONS + simulation, encoding='utf-8-sig')
            result = subprocess.run([switch.powershell_path(), '-NoProfile', '-NonInteractive',
                '-ExecutionPolicy', 'Bypass', '-File', str(path)], capture_output=True,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        data = json.loads(result.stdout.decode('utf-8-sig'))
        self.assertTrue(data['ok'] and data['disposed'])
        self.assertEqual(data['method'], 'HEAD')
        self.assertEqual(data['timeout'], 4000)
        self.assertFalse(data['redirects'] or data['failure'])
        self.assertEqual(data['proxy'], 'http://proxy.example:3128/')

    def test_locked_script_can_be_read_but_cannot_be_changed_or_deleted(self):
        with tempfile.TemporaryDirectory(prefix='ClipboardAI_LockTest_') as folder:
            path = Path(folder) / 'helper.ps1'
            with switch._locked_script(path, "Write-Output 'safe-offline-test'\n"):
                with self.assertRaises(OSError):
                    path.write_text("Write-Output 'changed'")
                with self.assertRaises(OSError):
                    path.unlink()
                result = subprocess.run([switch.powershell_path(), '-NoProfile', '-NonInteractive',
                    '-ExecutionPolicy', 'Bypass', '-File', str(path)], capture_output=True,
                    timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                self.assertIn(b'safe-offline-test', result.stdout)
            path.unlink()

    def transaction(self, connected=True, restore_fail=False, changed_ssid_only=False):
        simulation = r'''
$global:events=[Collections.Generic.List[string]]::new()
$global:current=@{profile='Original';ssid_hex='616263'}
$global:rows=@(
 [pscustomobject]@{id='11111111-1111-1111-1111-111111111111';kind='wifi';name='Wi-Fi';enabled=$true;status='Up';AdminStatus=1},
 [pscustomobject]@{id='22222222-2222-2222-2222-222222222222';kind='lan';name='LAN';enabled=$false;status='Disabled';AdminStatus=2})
function Read-PhysicalAdapters { @($global:rows | ForEach-Object { [pscustomobject]@{id=$_.id;kind=$_.kind;enabled=$_.enabled;status=$_.status} }) }
function Find-PhysicalAdapter($id) { $global:rows | Where-Object {$_.id -eq $id} }
function Test-AdapterReady($id) { return $true }
function Test-ProviderConnection($hostName) { return $false }
function Read-WifiConnection($id) { return $global:current.Clone() }
function Connect-WifiProfile($id,$profile) {
 if ($global:restoreFail) { throw 'synthetic_restore_failure' }
 $global:events.Add('connect:'+ $profile);$global:current=@{profile=$profile;ssid_hex='616263'}
}
function Disconnect-WifiProfile($id) { $global:events.Add('disconnect');$global:current=@{profile='';ssid_hex=''} }
function Enable-NetAdapter { param([Parameter(ValueFromPipeline=$true)]$InputObject,[switch]$Confirm) process {
 $InputObject.enabled=$true;$InputObject.AdminStatus=1;$InputObject.status='Up'
 if ($InputObject.kind -eq 'wifi') { $global:current=@{profile='Different auto-connect';ssid_hex='646566'} }
 if ($InputObject.kind -eq 'wifi' -and $global:changedSsidOnly) { $global:current.profile='Original' }
} }
function Disable-NetAdapter { param([Parameter(ValueFromPipeline=$true)]$InputObject,[switch]$Confirm) process {
 $InputObject.enabled=$false;$InputObject.AdminStatus=2;$InputObject.status='Disabled'
 if ($InputObject.kind -eq 'wifi') { $global:current=@{profile='';ssid_hex=''} }
} }
function Start-Sleep { param($Milliseconds) }
'''
        simulation += '$global:restoreFail=$' + ('true' if restore_fail else 'false') + '\n'
        simulation += '$global:changedSsidOnly=$' + ('true' if changed_ssid_only else 'false') + '\n'
        if not connected:
            simulation += "$global:current=@{profile='';ssid_hex=''}\n$global:rows[0].status='Disconnected'\n"
        simulation += r'''
$request=[pscustomobject]@{target_mode='lan';adapter_id='22222222-2222-2222-2222-222222222222';probe_host='no.real.network'}
$result=Invoke-NetworkSwitch $request
$result.connection=$global:current
$result.events=@($global:events.ToArray())
ConvertTo-Json $result -Depth 5 -Compress
'''
        with tempfile.TemporaryDirectory(prefix='ClipboardAI_RecoveryTest_') as folder:
            path = Path(folder) / 'mock.ps1'
            path.write_text(switch.POWERSHELL_FUNCTIONS + simulation, encoding='utf-8-sig')
            result = subprocess.run([switch.powershell_path(), '-NoProfile', '-NonInteractive',
                '-ExecutionPolicy', 'Bypass', '-File', str(path)], capture_output=True,
                timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        return json.loads(result.stdout.decode('utf-8-sig'))

    def test_source_wifi_restores_original_profile_and_ssid_after_autoconnect_changes(self):
        result = self.transaction()
        self.assertEqual(result['code'], 'provider_unreachable')
        self.assertEqual(result['connection'], dict(profile='Original', ssid_hex='616263'))
        self.assertEqual(result['events'], ['connect:Original'])
        self.assertEqual([row['kind'] for row in result['adapters'] if row['enabled']], ['wifi'])

    def test_same_profile_on_different_ssid_still_restores_original_ssid(self):
        result = self.transaction(changed_ssid_only=True)
        self.assertEqual(result['connection'], dict(profile='Original', ssid_hex='616263'))
        self.assertEqual(result['events'], ['connect:Original'])

    def test_source_wifi_originally_disconnected_is_not_left_autoconnected(self):
        result = self.transaction(connected=False)
        self.assertEqual(result['code'], 'provider_unreachable')
        self.assertEqual(result['connection']['profile'], '')
        self.assertEqual(result['events'], ['disconnect'])

    def test_source_profile_restore_failure_preserves_new_adapter_and_reports_failure(self):
        result = self.transaction(restore_fail=True)
        self.assertEqual(result['code'], 'rollback_failed')
        self.assertTrue(next(row['enabled'] for row in result['adapters'] if row['kind'] == 'lan'))


if __name__ == '__main__':
    unittest.main()
