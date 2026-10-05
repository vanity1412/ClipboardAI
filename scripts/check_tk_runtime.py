"""Validate external Tcl/Tk scripts or Tcl/Tk 9's DLL-embedded zipfs data."""
import json
import sys


def validate_runtime(info):
    if not info.available:
        raise RuntimeError('Cannot import/initialize tkinter and Tcl with this Python installation')
    for library, root, entry in (('tcl', info.TCL_ROOTNAME, 'init.tcl'),
                                  ('tk', info.TK_ROOTNAME, 'tk.tcl')):
        directory = getattr(info, library + '_data_dir')
        if getattr(info, library + '_data_missing'):
            raise RuntimeError(library + ' runtime data is missing: ' + str(directory))
        if isinstance(directory, str) and directory.startswith('//zipfs:/'):
            # PyInstaller deliberately collects no separate scripts for Tcl/Tk
            # 9 libraries that embed their data archive in the shared library.
            continue
        target = root + '/' + entry
        if not any(row[0].replace('\\', '/') == target for row in info.data_files):
            raise RuntimeError(library + ' runtime scripts were not collected: ' + str(directory))


def main():
    from PyInstaller.utils.hooks.tcl_tk import tcltk_info
    # Print enough diagnostics to distinguish missing Tk from embedded data.
    print(json.dumps({
        'python': sys.version.split()[0], 'python_executable': sys.executable,
        'available': tcltk_info.available,
        'tcl_version': tcltk_info.tcl_version, 'tk_version': tcltk_info.tk_version,
        'tcl_data_dir': tcltk_info.tcl_data_dir, 'tk_data_dir': tcltk_info.tk_data_dir,
        'tcl_data_missing': tcltk_info.tcl_data_missing,
        'tk_data_missing': tcltk_info.tk_data_missing,
        'collected_data_files': len(tcltk_info.data_files),
    }, ensure_ascii=True))
    validate_runtime(tcltk_info)
    print('Tcl/Tk runtime preflight: OK')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, AttributeError, ImportError) as exc:
        print('Tcl/Tk runtime preflight failed: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
