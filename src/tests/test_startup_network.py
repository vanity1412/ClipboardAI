import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from network_switch import NetworkManager, make_switch_script

ROWS = [dict(id='11111111-1111-1111-1111-111111111111', kind='wifi', name='Wi-Fi 1', enabled=True, status='Up'),
        dict(id='22222222-2222-2222-2222-222222222222', kind='wifi', name='Wi-Fi 2', enabled=False, status='Disabled'),
        dict(id='33333333-3333-3333-3333-333333333333', kind='lan', name='LAN', enabled=True, status='Up')]


class ManualNetworkTests(unittest.TestCase):
    def test_legacy_auto_setting_never_switches_at_startup(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'network_config.json').write_text(json.dumps(dict(prefer_wifi=True)))
            manager = NetworkManager(root)
            self.assertFalse(manager.prefer_wifi)
            with patch('network_switch.read_adapters', return_value=ROWS), patch('network_switch.run_switch') as switch:
                self.assertTrue(manager.perform('startup')['ok'])
                switch.assert_not_called()

    def test_multiple_cards_require_selection(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NetworkManager(root)
            with patch('network_switch.read_adapters', return_value=ROWS), patch('network_switch.run_switch') as switch:
                self.assertFalse(manager.perform('wifi')['ok'])
                self.assertFalse(manager.perform()['ok'])
                switch.assert_not_called()

    def test_selected_card_is_bound_to_fresh_inventory(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NetworkManager(root)
            with patch('network_switch.read_adapters', return_value=ROWS), patch('network_switch.run_switch', return_value=dict(ok=True, adapters=ROWS)) as switch:
                self.assertFalse(manager.perform('lan', adapter_id=ROWS[1]['id'])['ok'])
                switch.assert_not_called()
                self.assertTrue(manager.perform('wifi', adapter_id=ROWS[1]['id'])['ok'])
                switch.assert_called_once_with('wifi', 'api.deepseek.com', adapter_id=ROWS[1]['id'])

    def test_adapter_id_must_be_guid(self):
        with self.assertRaises(ValueError):
            make_switch_script('wifi', 'api.example', Path('result.json'), adapter_id='injected command')
