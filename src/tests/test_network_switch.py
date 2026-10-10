import base64
import copy
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from network_switch import (DoublePress, NetworkManager, POWERSHELL_FUNCTIONS,
                            encoded_command, make_switch_script, powershell_path, read_adapters)
from windows_native import WindowsApp

WIFI = '11111111-1111-1111-1111-111111111111'
LAN = '22222222-2222-2222-2222-222222222222'


def adapters():
    return [dict(id=WIFI, name='Wi-Fi', description='Wireless', kind='wifi', status='Up', enabled=True, index=1),
            dict(id=LAN, name='Ethernet', description='LAN', kind='lan', status='Up', enabled=True, index=2)]


class NetworkTests(unittest.TestCase):
    def test_double_press_single_slow_and_held_repeat_sequences(self):
        keys = DoublePress()
        self.assertFalse(keys.press(1))
        self.assertFalse(keys.press(2))
        self.assertTrue(keys.press(2.49))
        self.assertFalse(keys.press(2.6))
        self.assertTrue(keys.press(3.1))

    def test_startup_wifi_and_toggle_each_direction(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NetworkManager(root)
            rows = adapters()
            def switch(target, host, prefer_wifi=False, adapter_id=None):
                for row in rows:
                    row['enabled'] = row['kind'] == target
                    row['status'] = 'Up' if row['enabled'] else 'Disabled'
                return dict(ok=True, adapters=copy.deepcopy(rows))
            with patch('network_switch.read_adapters', side_effect=lambda: copy.deepcopy(rows)), patch('network_switch.run_switch', side_effect=switch) as run:
                self.assertTrue(manager.perform('startup')['ok'])
                run.assert_not_called()
                self.assertTrue(manager.perform('lan')['ok'])
                self.assertEqual(run.call_args.args[0], 'lan')
                self.assertTrue(manager.perform('wifi')['ok'])
                self.assertEqual(run.call_args.args[0], 'wifi')

    def test_read_refresh_and_already_wifi_need_no_elevation(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NetworkManager(root)
            rows = adapters()
            rows[1].update(enabled=False, status='Disabled')
            with patch('network_switch.read_adapters', return_value=rows), patch('network_switch.run_switch') as run:
                self.assertTrue(manager.perform('refresh')['ok'])
                self.assertTrue(manager.perform('startup')['ok'])
                run.assert_not_called()

    def test_legacy_selection_does_not_exclude_additional_ethernet_and_config_survives(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'network_config.json').write_text(json.dumps({'wifi': WIFI, 'lan': LAN, 'prefer_wifi': True}), encoding='utf-8')
            manager = NetworkManager(root)
            rows = adapters()
            rows[1].update(enabled=False, status='Disabled')
            rows.append(dict(rows[1], id='33333333-3333-3333-3333-333333333333', name='Ethernet 3', enabled=True, status='Up'))
            with patch('network_switch.read_adapters', return_value=rows), patch('network_switch.run_switch', return_value={'ok': True, 'mode': 'wifi', 'adapters': rows}) as run:
                self.assertTrue(manager.perform('wifi')['ok'])
                run.assert_called_once_with('wifi', 'api.deepseek.com', adapter_id=WIFI)
            manager.prefer_wifi = False
            manager.save()
            restored = NetworkManager(root)
            self.assertFalse(restored.prefer_wifi)
            self.assertEqual(json.loads(manager.path.read_text())['adapter_policy'], 'all_physical')

    def test_uac_cancel_missing_adapter_and_read_failure_keep_mode(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NetworkManager(root)
            manager.mode = 'wifi'
            with patch('network_switch.read_adapters', return_value=adapters()), patch('network_switch.run_switch', side_effect=PermissionError('uac_denied')):
                result = manager.perform('lan')
                self.assertFalse(result['ok'])
                self.assertIn('UAC', result['message'])
                self.assertEqual(manager.mode, 'wifi')
            with patch('network_switch.read_adapters', return_value=[]), patch('network_switch.run_switch') as run:
                self.assertFalse(manager.perform('wifi')['ok'])
                run.assert_not_called()
            with patch('network_switch.read_adapters', side_effect=OSError('adapter_read_failed')):
                self.assertFalse(manager.perform('wifi')['ok'])

    def test_script_payload_is_data_not_powershell(self):
        host = "api.example.com'; Start-Process calc; '"
        script = make_switch_script('wifi', host, Path('result.json'))
        self.assertNotIn(host, script)
        self.assertEqual(base64.b64decode(encoded_command(script)).decode('utf-16-le'), script)
        with self.assertRaises(ValueError):
            make_switch_script('Wi-Fi; anything', 'api.deepseek.com', Path('result.json'))

    def test_read_adapter_invocation_has_no_mutation_command(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps(adapters()).encode(), b'')
        with patch('network_switch.subprocess.run', return_value=completed) as run:
            self.assertEqual(read_adapters()[0]['id'], WIFI)
        command = base64.b64decode(run.call_args.args[0][-1]).decode('utf-16-le')
        # Mutation code is only defined, never invoked, during inventory reads.
        self.assertTrue(command.rstrip().endswith('ConvertTo-Json -InputObject @(Read-PhysicalAdapters) -Depth 4 -Compress'))


class HotkeyTests(unittest.TestCase):
    def app(self):
        app = WindowsApp.__new__(WindowsApp)
        app.busy = app.probe_busy = app.network_busy = False
        app.enabled = True
        app.config = {}
        app.network_press = DoublePress()
        app.network = Mock()
        app.network.perform.return_value = {'ok': True, 'message': 'Đã chuyển sang LAN'}
        app.results = queue.Queue()
        app.tooltip, app.refresh_panel = Mock(), Mock()
        return app

    def test_f3_dispatch_requires_double_press(self):
        app = self.app()
        app.open_network_picker = Mock()
        with patch('windows_native.time.monotonic', side_effect=[1, 1.2]), patch('windows_native.log_event'):
            app.window_proc(1, 0x0312, 209, 0)
            app.open_network_picker.assert_not_called()
            app.window_proc(1, 0x0312, 209, 0)
            app.open_network_picker.assert_called_once_with()

    def test_ai_busy_paused_and_network_busy_block_switch(self):
        app = self.app()
        for attribute in ('busy', 'probe_busy', 'network_busy'):
            setattr(app, attribute, True)
            app.start_network()
            app.network.perform.assert_not_called()
            setattr(app, attribute, False)
        app.enabled = False
        app.start_network()
        app.network.perform.assert_not_called()

    def test_network_worker_is_async_and_does_not_clear_session(self):
        app = self.app()
        original = object()
        app.session = original
        entered, release = threading.Event(), threading.Event()
        def operation(*args, **kwargs):
            entered.set()
            release.wait(2)
            return {'ok': True, 'message': 'Đã chuyển sang LAN'}
        app.network.perform.side_effect = operation
        app.start_network()
        self.assertTrue(entered.wait(1))
        self.assertTrue(app.network_busy)
        self.assertIs(app.session, original)
        release.set()
        result = app.results.get(timeout=2)
        self.assertEqual(result[0], 'network')

    def test_request_during_switch_never_cancels_or_sends_ai(self):
        app = self.app()
        app.network_busy = True
        app.cancel_request = Mock()
        app.start_request('new statement', replace=True)
        app.cancel_request.assert_not_called()
        self.assertIn('mạng', app.state)

    def test_f3_registers_without_autorepeat(self):
        app = self.app()
        app.hwnd, app.user, app.hotkeys, app.hotkey_errors = 1, Mock(), [], []
        app.user.RegisterHotKey.return_value = True
        app.register_hotkeys()
        app.user.RegisterHotKey.assert_any_call(1, 209, 0x4000, 0x72)
        self.assertEqual(len(app.hotkeys), 9)
        app.user.RegisterHotKey.assert_any_call(app.hwnd, 213, 0x4004, 0x79)


@unittest.skipUnless(os.name == 'nt', 'PowerShell transaction simulation')
class PowerShellTransactionTests(unittest.TestCase):
    def run_transaction(self, mode):
        # Every system/network cmdlet and connectivity probe below is replaced.
        # This test cannot change the user's real network or access the internet.
        simulation = r"""
$global:events = [Collections.Generic.List[string]]::new()
$global:rows = @(
    [pscustomobject]@{InterfaceGuid='11111111-1111-1111-1111-111111111111'; Name='Wi-Fi'; InterfaceDescription='Wi-Fi'; InterfaceType=71; NdisPhysicalMedium=9; HardwareInterface=$true; Virtual=$false; Status='Disabled'; AdminStatus=2; ifIndex=1},
    [pscustomobject]@{InterfaceGuid='22222222-2222-2222-2222-222222222222'; Name='Ethernet'; InterfaceDescription='LAN'; InterfaceType=6; NdisPhysicalMedium=14; HardwareInterface=$true; Virtual=$false; Status='Up'; AdminStatus=1; ifIndex=2},
    [pscustomobject]@{InterfaceGuid='33333333-3333-3333-3333-333333333333'; Name='vEthernet'; InterfaceDescription='Virtual'; InterfaceType=6; NdisPhysicalMedium=14; HardwareInterface=$false; Virtual=$true; Status='Up'; AdminStatus=1; ifIndex=3}
)
function Get-NetAdapter { param([switch]$Physical) $global:rows }
function Get-NetIPConfiguration { param($InterfaceIndex) [pscustomobject]@{IPv4Address=@([pscustomobject]@{IPAddress='192.0.2.1'}); IPv4DefaultGateway=[pscustomobject]@{NextHop='192.0.2.254'}; IPv6Address=@(); IPv6DefaultGateway=$null} }
function Enable-NetAdapter { param([Parameter(ValueFromPipeline=$true)]$InputObject, [switch]$Confirm) process {
    if ($global:mode -eq 'rollback_failure' -and $InputObject.Name -eq 'Ethernet') { throw 'synthetic rollback failure' }
    $global:events.Add('enable:' + $InputObject.Name); $InputObject.AdminStatus=1; $InputObject.Status='Up'
} }
function Disable-NetAdapter { param([Parameter(ValueFromPipeline=$true)]$InputObject, [switch]$Confirm) process {
    $global:events.Add('disable:' + $InputObject.Name)
    if ($global:mode -eq 'disable_failure' -and $InputObject.Name -eq 'Ethernet') { throw 'synthetic error' }
    if ($global:mode -eq 'group_disable_failure' -and $InputObject.Name -eq 'Ethernet 3') { throw 'synthetic second NIC error' }
    $InputObject.AdminStatus=2; $InputObject.Status='Disabled'
} }
function Test-ProviderConnection { param($hostName) $global:events.Add('probe'); return ($global:mode -notin @('provider_failure','rollback_failure')) }
function Read-WifiConnection { param($id) return @{profile='';ssid_hex=''} }
function Connect-WifiProfile { param($id,$profile) }
function Disconnect-WifiProfile { param($id) }
function Start-Sleep { param($Milliseconds) }
if ($global:mode -eq 'not_ready') { function Test-AdapterReady { param($id) throw 'target_unavailable' } }
if ($global:mode -eq 'wifi_fallback') {
    $global:rows[1].Status = 'Disabled'; $global:rows[1].AdminStatus = 2
    function Test-AdapterReady { param($id) if ($id -eq '11111111-1111-1111-1111-111111111111') { throw 'target_unavailable' }; return $true }
}
if ($global:mode -in @('all_wifi_success','all_lan_success','group_disable_failure','selected_wifi','selected_lan')) {
    $global:rows += [pscustomobject]@{InterfaceGuid='55555555-5555-5555-5555-555555555555'; Name='Ethernet 3'; InterfaceDescription='Remote NDIS Compatible Device'; InterfaceType=6; NdisPhysicalMedium=14; HardwareInterface=$true; Virtual=$false; Status='Up'; AdminStatus=1; ifIndex=5}
}
if ($global:mode -in @('all_wifi_success','selected_wifi','selected_lan')) {
    $global:rows += [pscustomobject]@{InterfaceGuid='44444444-4444-4444-4444-444444444444'; Name='Wi-Fi 2'; InterfaceDescription='USB Wi-Fi'; InterfaceType=71; NdisPhysicalMedium=9; HardwareInterface=$true; Virtual=$false; Status='Disabled'; AdminStatus=2; ifIndex=4}
}
if ($global:mode -in @('all_lan_success','selected_wifi','selected_lan')) {
    $global:rows[0].Status='Up'; $global:rows[0].AdminStatus=1
    $global:rows[1].Status='Disabled'; $global:rows[1].AdminStatus=2
}
$targetMode = if ($global:mode -in @('all_lan_success','selected_lan')) { 'lan' } else { 'wifi' }
$request = [pscustomobject]@{target_mode=$targetMode; probe_host='no.real.network'}
if ($global:mode -eq 'selected_wifi') { $request | Add-Member adapter_id '44444444-4444-4444-4444-444444444444' }
if ($global:mode -eq 'selected_lan') { $request | Add-Member adapter_id '55555555-5555-5555-5555-555555555555' }
$result = if ($global:mode -eq 'wifi_fallback') { Invoke-WifiPriority $request } else { Invoke-NetworkSwitch $request }
$result.events = @($global:events.ToArray())
ConvertTo-Json -InputObject $result -Depth 5 -Compress
"""
        script = POWERSHELL_FUNCTIONS + "\n$global:mode='" + mode + "'\n" + simulation
        # Long transaction + mocks must not exceed Windows' 32K command limit.
        with tempfile.TemporaryDirectory(prefix='ClipboardAI_NetworkTest_') as folder:
            script_path = Path(folder) / 'transaction.ps1'
            script_path.write_text(script, encoding='utf-8-sig')
            result = subprocess.run([powershell_path(), '-NoProfile', '-NonInteractive',
                                     '-ExecutionPolicy', 'Bypass', '-File', str(script_path)],
                                    capture_output=True, timeout=20,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        return json.loads(result.stdout.decode('utf-8-sig'))

    def test_target_enabled_before_old_disabled_and_virtual_card_untouched(self):
        result = self.run_transaction('success')
        self.assertTrue(result['ok'])
        self.assertEqual(result['events'], ['enable:Wi-Fi', 'disable:Ethernet', 'probe'])
        self.assertEqual(len(result['adapters']), 2)

    def test_failed_connect_restores_old_before_disabling_new(self):
        result = self.run_transaction('provider_failure')
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'provider_unreachable')
        self.assertEqual(result['events'][-2:], ['enable:Ethernet', 'disable:Wi-Fi'])
        states = {row['kind']: row['enabled'] for row in result['adapters']}
        self.assertEqual(states, {'wifi': False, 'lan': True})

    def test_unavailable_target_does_not_disable_old_network(self):
        result = self.run_transaction('not_ready')
        self.assertFalse(result['ok'])
        self.assertNotIn('disable:Ethernet', result['events'])
        self.assertEqual(result['events'], ['enable:Wi-Fi', 'disable:Wi-Fi'])

    def test_disable_failure_restores_initial_state(self):
        result = self.run_transaction('disable_failure')
        self.assertFalse(result['ok'])
        self.assertEqual(result['events'][-2:], ['enable:Ethernet', 'disable:Wi-Fi'])

    def test_rollback_failure_keeps_new_card_enabled_and_reports_manual_recovery(self):
        result = self.run_transaction('rollback_failure')
        self.assertEqual(result['code'], 'rollback_failed')
        self.assertNotIn('disable:Wi-Fi', result['events'])
        self.assertTrue(next(row['enabled'] for row in result['adapters'] if row['kind'] == 'wifi'))

    def test_startup_falls_back_to_lan_disabled_by_previous_session(self):
        result = self.run_transaction('wifi_fallback')
        self.assertTrue(result['ok'])
        self.assertEqual(result['mode'], 'lan')
        self.assertEqual(result['code'], 'fallback_lan')
        self.assertEqual(result['events'], ['enable:Wi-Fi', 'disable:Wi-Fi', 'enable:Ethernet', 'probe'])

    def test_wifi_enables_all_wireless_and_disables_every_ethernet(self):
        result = self.run_transaction('all_wifi_success')
        self.assertTrue(result['ok'])
        self.assertTrue(all(row['enabled'] for row in result['adapters'] if row['kind'] == 'wifi'))
        self.assertTrue(all(not row['enabled'] for row in result['adapters'] if row['kind'] == 'lan'))
        self.assertEqual(result['events'], ['enable:Wi-Fi', 'enable:Wi-Fi 2', 'disable:Ethernet', 'disable:Ethernet 3', 'probe'])

    def test_reverse_enables_all_ethernet_including_usb_and_disables_wifi(self):
        result = self.run_transaction('all_lan_success')
        self.assertTrue(result['ok'])
        self.assertEqual(result['mode'], 'lan')
        self.assertTrue(all(row['enabled'] for row in result['adapters'] if row['kind'] == 'lan'))
        self.assertTrue(all(not row['enabled'] for row in result['adapters'] if row['kind'] == 'wifi'))

    def test_partial_group_failure_restores_every_adapter(self):
        result = self.run_transaction('group_disable_failure')
        self.assertFalse(result['ok'])
        self.assertEqual({row['name']: row['enabled'] for row in result['adapters']},
                         {'Wi-Fi': False, 'Ethernet': True, 'Ethernet 3': True})
        self.assertNotIn('disable:vEthernet', result['events'])

    def test_selected_wifi_enables_only_selected_wifi_and_disables_other_wifi_and_all_lan(self):
        result = self.run_transaction('selected_wifi')
        self.assertTrue(result['ok'])
        self.assertEqual([row['name'] for row in result['adapters'] if row['enabled']], ['Wi-Fi 2'])
        self.assertIn('disable:Wi-Fi', result['events'])
        self.assertIn('disable:Ethernet 3', result['events'])

    def test_selected_lan_enables_only_selected_lan_and_disables_wifi_and_other_lan(self):
        result = self.run_transaction('selected_lan')
        self.assertTrue(result['ok'])
        self.assertEqual([row['name'] for row in result['adapters'] if row['enabled']], ['Ethernet 3'])
        self.assertIn('disable:Wi-Fi', result['events'])


if __name__ == '__main__':
    unittest.main()
