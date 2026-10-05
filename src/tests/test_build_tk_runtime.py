import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

path = Path(__file__).resolve().parents[2] / 'scripts' / 'check_tk_runtime.py'
spec = importlib.util.spec_from_file_location('check_tk_runtime', path)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def info(**changes):
    values = dict(available=True, TCL_ROOTNAME='_tcl_data', TK_ROOTNAME='_tk_data',
                  tcl_data_dir='C:/Python/tcl/tcl8.6', tk_data_dir='C:/Python/tcl/tk8.6',
                  tcl_data_missing=False, tk_data_missing=False,
                  data_files=[('_tcl_data/init.tcl', 'C:/Python/tcl/tcl8.6/init.tcl', 'DATA'),
                              ('_tk_data/tk.tcl', 'C:/Python/tcl/tk8.6/tk.tcl', 'DATA')])
    values.update(changes)
    return SimpleNamespace(**values)


class TkBuildTests(unittest.TestCase):
    def test_external_scripts_are_accepted(self):
        preflight.validate_runtime(info())

    def test_embedded_tcl_tk9_with_no_data_files_are_accepted(self):
        preflight.validate_runtime(info(tcl_data_dir='//zipfs:/lib/tcl/tcl_library',
                                       tk_data_dir='//zipfs:/lib/tk/tk_library', data_files=[]))

    def test_mixed_embedded_and_external_libraries_are_accepted(self):
        preflight.validate_runtime(info(tcl_data_dir='//zipfs:/lib/tcl/tcl_library',
                                       data_files=[('_tk_data/tk.tcl', 'tk.tcl', 'DATA')]))
        preflight.validate_runtime(info(tk_data_dir='//zipfs:/lib/tk/tk_library',
                                       data_files=[('_tcl_data/init.tcl', 'init.tcl', 'DATA')]))

    def test_unavailable_or_missing_runtime_is_rejected(self):
        for changes in ({'available': False}, {'tcl_data_missing': True}, {'tk_data_missing': True}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                preflight.validate_runtime(info(**changes))

    def test_missing_external_entrypoints_are_rejected_even_with_other_files(self):
        for files in ([], [('_tcl_data/other.tcl', 'other.tcl', 'DATA')],
                      [('_tcl_data/init.tcl', 'init.tcl', 'DATA')]):
            with self.subTest(files=files), self.assertRaises(RuntimeError):
                preflight.validate_runtime(info(data_files=files))

    def test_windows_separators_in_collection_are_accepted(self):
        preflight.validate_runtime(info(data_files=[('_tcl_data\\init.tcl', 'init.tcl', 'DATA'),
                                                    ('_tk_data\\tk.tcl', 'tk.tcl', 'DATA')]))


if __name__ == '__main__':
    unittest.main()
