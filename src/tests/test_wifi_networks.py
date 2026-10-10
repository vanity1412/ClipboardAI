"""All network mutations, native WLAN calls, and TCP probes are mocked."""
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import network_switch as switch
from wifi_networks import (AvailableNetwork, Guid, ListHeader, NativeWlan,
                           network_row, normalize_networks, read_wifi_networks)


WIFI = '11111111-1111-1111-1111-111111111111'
LAN = '22222222-2222-2222-2222-222222222222'
ADAPTERS = [dict(id=WIFI, kind='wifi', name='Wi-Fi', enabled=True, status='Up'),
            dict(id=LAN, kind='lan', name='Ethernet', enabled=True, status='Up')]


def network(name='Mạng học tập', profile='Hồ sơ đã lưu', saved=True):
    row = AvailableNetwork()
    raw = name.encode('utf-8')
    row.ssid.length = len(raw)
    row.ssid.data[:len(raw)] = raw
    row.profile = profile if saved else ''
    row.flags = 2 if saved else 0
    row.bss_type, row.signal, row.connectable, row.secure = 1, 80, True, True
    return network_row(row)


class WifiMetadataTests(unittest.TestCase):
    def test_unicode_ssid_profile_and_exact_raw_id(self):
        row = network()
        self.assertEqual(row['ssid'], 'Mạng học tập')
        self.assertEqual(row['profile'], 'Hồ sơ đã lưu')
        self.assertEqual(row['ssid_hex'], 'Mạng học tập'.encode().hex().upper())
        self.assertTrue(row['saved'] and row['connectable'])

    def test_unsaved_secured_network_requires_windows_setup(self):
        row = network(saved=False)
        self.assertFalse(row['connectable'])
        self.assertTrue(row['setup_required'])
        self.assertEqual(row['reason'], 'windows_setup_required')

    def test_unsaved_duplicates_do_not_hide_saved_profile(self):
        saved, unsaved = network(), network(saved=False)
        self.assertEqual(normalize_networks([unsaved, saved, dict(saved)]), [saved])

    def test_permission_location_error_is_actionable(self):
        with self.assertRaisesRegex(OSError, 'wifi_permission'):
            NativeWlan.check(5)
        self.assertIn('Vị trí', switch.NETWORK_MESSAGES['wifi_permission'])

    @unittest.skipUnless(os.name == 'nt', 'Windows structure layout')
    def test_windows_wlan_layout(self):
        self.assertEqual(C.sizeof(Guid), 16)
        self.assertEqual(C.sizeof(ListHeader), 8)
        self.assertEqual(C.sizeof(AvailableNetwork), 628)

    def test_stalled_native_read_is_bounded_without_network_mutation(self):
        release = threading.Event()
        with patch('wifi_networks.NativeWlan') as native:
            native.return_value.networks.side_effect = lambda ident: release.wait(1)
            try:
                start = time.monotonic()
                with self.assertRaisesRegex(TimeoutError, 'wifi_read_timeout'):
                    read_wifi_networks(WIFI, timeout=.01)
                self.assertLess(time.monotonic() - start, .5)
            finally:
                release.set()

    @unittest.skipUnless(os.name == 'nt', 'Windows native declarations')
    def test_native_listing_uses_structured_buffer_and_frees_handle(self):
        api = Mock()
        raw = AvailableNetwork()
        raw.profile, raw.flags, raw.bss_type, raw.connectable = 'Saved Unicode', 2, 1, True
        raw.ssid.length, raw.ssid.data[:3] = 3, b'abc'
        buffer = C.create_string_buffer(C.sizeof(ListHeader) + C.sizeof(raw))
        ListHeader.from_buffer(buffer).count = 1
        C.memmove(C.addressof(buffer) + C.sizeof(ListHeader), C.byref(raw), C.sizeof(raw))

        def available(handle, guid, flags, reserved, data):
            C.cast(data, C.POINTER(C.c_void_p))[0] = C.addressof(buffer)
            return 0

        api.WlanOpenHandle.return_value = 0
        api.WlanGetAvailableNetworkList.side_effect = available
        with patch('wifi_networks.C.WinDLL', return_value=api):
            rows = NativeWlan().networks(WIFI)
        self.assertEqual(rows[0]['ssid'], 'abc')
        self.assertEqual(rows[0]['profile'], 'Saved Unicode')
        api.WlanFreeMemory.assert_called_once()
        api.WlanCloseHandle.assert_called_once()
        self.assertEqual(len(api.WlanGetAvailableNetworkList.argtypes), 5)


class WifiManagerTests(unittest.TestCase):
    def test_scan_is_read_only_and_returns_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            manager = switch.NetworkManager(root)
            rows = [network()]
            with patch('network_switch.read_adapters', return_value=ADAPTERS), patch('network_switch.read_wifi_networks', return_value=rows), patch('network_switch.run_switch') as operation:
                result = manager.perform('wifi_scan', adapter_id=WIFI)
            self.assertTrue(result['ok'])
            self.assertEqual(result['networks'], rows)
            operation.assert_not_called()

    def test_disabled_scan_does_not_enable_adapter(self):
        with tempfile.TemporaryDirectory() as root:
            manager = switch.NetworkManager(root)
            rows = [dict(ADAPTERS[0], enabled=False), ADAPTERS[1]]
            with patch('network_switch.read_adapters', return_value=rows), patch('network_switch.read_wifi_networks') as scan, patch('network_switch.run_switch') as operation:
                result = manager.perform('wifi_scan', adapter_id=WIFI)
            self.assertEqual(result['code'], 'wifi_disabled')
            scan.assert_not_called()
            operation.assert_not_called()

    def test_saved_profile_connect_uses_fresh_exact_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            manager = switch.NetworkManager(root)
            row = network()
            with patch('network_switch.read_adapters', return_value=ADAPTERS), patch('network_switch.read_wifi_networks', return_value=[row]), patch('network_switch.run_switch', return_value=dict(ok=True, adapters=ADAPTERS)) as operation:
                result = manager.perform('wifi_connect', adapter_id=WIFI, profile_name=row['profile'], ssid=row['ssid'])
            self.assertTrue(result['ok'])
            operation.assert_called_once_with('wifi', 'api.deepseek.com', adapter_id=WIFI,
                         wifi_profile=row['profile'], ssid_hex=row['ssid_hex'])

    def test_explicit_prepare_enables_disabled_wifi_then_reads_networks(self):
        with tempfile.TemporaryDirectory() as root:
            manager = switch.NetworkManager(root)
            rows = [dict(ADAPTERS[0], enabled=False), ADAPTERS[1]]
            with patch('network_switch.read_adapters', return_value=rows), \
                    patch('network_switch.read_wifi_networks', return_value=[network()]) as scan, \
                    patch('network_switch.run_switch', return_value=dict(ok=True, adapters=ADAPTERS)) as operation:
                result = manager.perform('wifi_prepare_scan', adapter_id=WIFI)
            self.assertTrue(result['ok'])
            operation.assert_called_once_with('wifi_prepare', 'api.deepseek.com', adapter_id=WIFI)
            scan.assert_called_once_with(WIFI)

    def test_failed_prepare_never_scans_or_connects(self):
        with tempfile.TemporaryDirectory() as root:
            manager = switch.NetworkManager(root)
            rows = [dict(ADAPTERS[0], enabled=False), ADAPTERS[1]]
            with patch('network_switch.read_adapters', return_value=rows), \
                    patch('network_switch.read_wifi_networks') as scan, \
                    patch('network_switch.run_switch', return_value=dict(ok=False, code='uac_denied')):
                result = manager.perform('wifi_prepare_scan', adapter_id=WIFI)
            self.assertFalse(result['ok'])
            self.assertEqual(result['code'], 'uac_denied')
            scan.assert_not_called()

    def test_unsaved_and_stale_networks_never_start_a_transaction(self):
        with tempfile.TemporaryDirectory() as root:
            manager = switch.NetworkManager(root)
            with patch('network_switch.read_adapters', return_value=ADAPTERS), patch('network_switch.read_wifi_networks', return_value=[network(saved=False)]), patch('network_switch.run_switch') as operation:
                result = manager.perform('wifi_connect', adapter_id=WIFI, profile_name='', ssid='Mạng học tập')
                self.assertEqual(result['code'], 'windows_setup_required')
                result = manager.perform('wifi_connect', adapter_id=WIFI, profile_name='profile changed')
                self.assertEqual(result['code'], 'wifi_network_missing')
            operation.assert_not_called()

    def test_pending_recovery_blocks_only_mutation_then_refresh_reports_finish(self):
        with tempfile.TemporaryDirectory() as root:
            manager = switch.NetworkManager(root)
            with patch('network_switch.network_recovery_pending', return_value=True), patch('network_switch.read_adapters', return_value=ADAPTERS), patch('network_switch.read_wifi_networks', return_value=[network()]), patch('network_switch.run_switch') as operation:
                self.assertTrue(manager.perform('refresh')['ok'])
                self.assertTrue(manager.perform('wifi_scan', adapter_id=WIFI)['ok'])
                self.assertEqual(manager.perform('lan')['code'], 'switch_recovery_pending')
                operation.assert_not_called()
            with patch('network_switch.read_adapters', return_value=ADAPTERS), patch('network_switch._last_switch_outcome', dict(ok=False, code='switch_timeout')):
                result = manager.perform('refresh')
            self.assertTrue(result['recovery_finished'])
            self.assertFalse(result['operation_ok'])


class BoundedTransactionTests(unittest.TestCase):
    def test_stalled_helper_requests_cooperative_rollback_keeps_resources(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        paths = {}

        def helper(*args):
            paths['result'], paths['cancel'] = args[-2:]
            entered.set()
            release.wait(2)
            self.assertTrue(paths['cancel'].exists())
            finished.set()
            return dict(ok=False, code='switch_timeout')

        with patch('network_switch._execute_switch', side_effect=helper), patch('network_switch.HELPER_TIMEOUT', .01), patch('network_switch.ROLLBACK_GRACE', .01):
            try:
                result = switch.run_switch('wifi', 'no.real.network', adapter_id=WIFI)
                self.assertTrue(entered.is_set())
                self.assertEqual(result['code'], 'switch_recovery_pending')
                self.assertTrue(switch.network_recovery_pending())
                self.assertTrue(paths['result'].exists())
                self.assertEqual(switch.run_switch('lan', 'no.real.network')['code'], 'switch_recovery_pending')
            finally:
                release.set()
                self.assertTrue(finished.wait(1))
                deadline = time.monotonic() + 1
                while switch.network_recovery_pending() and time.monotonic() < deadline:
                    time.sleep(.005)
        self.assertFalse(switch.network_recovery_pending())
        self.assertFalse(paths['result'].exists())

    def test_payload_does_not_interpolate_ssid_or_profile_as_code(self):
        profile = "'; Start-Process calc; '"
        script = switch.make_switch_script('wifi', 'no.real.network', Path('result.json'),
                                          adapter_id=WIFI, wifi_profile=profile, ssid_hex='616263')
        self.assertNotIn(profile, script)
        self.assertNotIn('netsh', script)


@unittest.skipUnless(os.name == 'nt', 'PowerShell mock transaction')
class SavedWifiTransactionTests(unittest.TestCase):
    def transaction(self, fail=False, cancel=False, initially_connected=True):
        # All adapters, WLAN connection, IP readiness and API probe are fake.
        script = switch.POWERSHELL_FUNCTIONS + r'''
$global:events = [Collections.Generic.List[string]]::new()
$global:current = 'Old profile'
$global:rows = @(
 [pscustomobject]@{id='11111111-1111-1111-1111-111111111111';kind='wifi';name='Wi-Fi';enabled=$true;status='Up'},
 [pscustomobject]@{id='22222222-2222-2222-2222-222222222222';kind='lan';name='LAN';enabled=$true;status='Up'})
function Read-PhysicalAdapters { @($global:rows | ForEach-Object { [pscustomobject]@{id=$_.id;kind=$_.kind;name=$_.name;enabled=$_.enabled;status=$_.status} }) }
function Find-PhysicalAdapter($id) { $global:rows | Where-Object { $_.id -eq $id } }
function Test-AdapterReady($id) { return $true }
function Connect-WifiProfile($id,$profile) { $global:events.Add('connect:'+$profile); $global:current=$profile }
function Read-WifiConnection($id) { return @{profile=$global:current;ssid_hex='616263'} }
function Disconnect-WifiProfile($id) { $global:events.Add('disconnect'); $global:current='' }
function Enable-NetAdapter { param([Parameter(ValueFromPipeline=$true)]$InputObject,[switch]$Confirm) process { $global:events.Add('enable:'+$InputObject.name);$InputObject.enabled=$true;$InputObject | Add-Member AdminStatus 1 -Force } }
function Disable-NetAdapter { param([Parameter(ValueFromPipeline=$true)]$InputObject,[switch]$Confirm) process { $global:events.Add('disable:'+$InputObject.name);$InputObject.enabled=$false } }
function Start-Sleep { param($Milliseconds) }
'''
        if not initially_connected:
            script += "$global:current=''\n"
        script += '\nfunction Test-ProviderConnection($hostName) { $global:events.Add(\'probe\'); return $' + ('false' if fail else 'true') + ' }\n'
        if cancel:
            script += "function Test-SwitchDeadline($request,$deadline) { throw 'switch_timeout' }\n"
        script += "$request=[pscustomobject]@{target_mode='wifi';adapter_id='" + WIFI + "';wifi_profile='New profile';ssid_hex='616263';probe_host='no.real.network'}\n"
        script += "$result=Invoke-NetworkSwitch $request; $result.events=@($global:events.ToArray());$result.profile=$global:current; ConvertTo-Json $result -Depth 5 -Compress\n"
        with tempfile.TemporaryDirectory(prefix='ClipboardAI_WifiTest_') as folder:
            path = Path(folder) / 'mock.ps1'
            path.write_text(script, encoding='utf-8-sig')
            result = subprocess.run([switch.powershell_path(), '-NoProfile', '-NonInteractive',
                     '-ExecutionPolicy', 'Bypass', '-File', str(path)], capture_output=True,
                     timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        return json.loads(result.stdout.decode('utf-8-sig'))

    def test_associate_saved_wifi_before_disabling_lan(self):
        result = self.transaction()
        self.assertTrue(result['ok'])
        self.assertEqual(result['events'], ['connect:New profile', 'disable:LAN', 'probe'])

    def test_failure_restores_previous_profile_and_lan(self):
        result = self.transaction(fail=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'provider_unreachable')
        self.assertEqual(result['events'][-2:], ['enable:LAN', 'connect:Old profile'])
        self.assertEqual(result['profile'], 'Old profile')

    def test_cancel_before_start_never_mutates_a_card(self):
        result = self.transaction(cancel=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'switch_timeout')
        self.assertEqual(result['events'], [])

    def test_failed_new_association_restores_original_disconnected_state(self):
        result = self.transaction(fail=True, initially_connected=False)
        self.assertFalse(result['ok'])
        self.assertEqual(result['profile'], '')
        self.assertEqual(result['events'][-2:], ['enable:LAN', 'disconnect'])

    def test_wlan_pinvoke_helper_compiles_without_calling_native_network(self):
        # Compilation and type lookup only; never invokes a native WLAN method.
        source = switch.WLAN_HELPER_SOURCE
        script = "Add-Type -TypeDefinition @'\n" + source + "\n'@\n[ClipboardWifi].FullName\n"
        with tempfile.TemporaryDirectory(prefix='ClipboardAI_WlanCompile_') as folder:
            path = Path(folder) / 'compile.ps1'
            path.write_text(script, encoding='utf-8-sig')
            result = subprocess.run([switch.powershell_path(), '-NoProfile', '-NonInteractive',
                     '-ExecutionPolicy', 'Bypass', '-File', str(path)], capture_output=True,
                     timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertIn(b'ClipboardWifi', result.stdout)


if __name__ == '__main__':
    unittest.main()
