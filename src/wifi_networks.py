"""Read Windows WLAN network metadata without parsing localized command output.

Only visible/manual-hidden network metadata is read. No profile XML or key
material is requested. Connecting saved profiles belongs to the adapter
transaction in network_switch, so listing never enables a card or changes Wi-Fi.
"""
import ctypes as C
from ctypes import wintypes as W
import queue
import threading
from uuid import UUID


WIFI_READ_TIMEOUT = 10
_read_lock = threading.Lock()


class Guid(C.Structure):
    _fields_ = [('data1', W.DWORD), ('data2', W.WORD), ('data3', W.WORD),
                ('data4', C.c_ubyte * 8)]

    @classmethod
    def from_id(cls, value):
        return cls.from_buffer_copy(UUID(value).bytes_le)


class Dot11Ssid(C.Structure):
    _fields_ = [('length', W.DWORD), ('data', C.c_ubyte * 32)]


class AvailableNetwork(C.Structure):
    # Layout from WLAN_AVAILABLE_NETWORK, wlanapi.h. Windows WCHAR is UTF-16.
    _fields_ = [('profile', W.WCHAR * 256), ('ssid', Dot11Ssid),
                ('bss_type', W.DWORD), ('bssid_count', W.DWORD),
                ('connectable', W.BOOL), ('reason_code', W.DWORD),
                ('phy_count', W.DWORD), ('phy_types', W.DWORD * 8),
                ('more_phy_types', W.BOOL), ('signal', W.DWORD),
                ('secure', W.BOOL), ('auth', W.DWORD), ('cipher', W.DWORD),
                ('flags', W.DWORD), ('reserved', W.DWORD)]


class ListHeader(C.Structure):
    _fields_ = [('count', W.DWORD), ('index', W.DWORD)]


def network_row(network):
    raw = bytes(network.ssid.data[:min(32, network.ssid.length)])
    ssid = raw.decode('utf-8', errors='backslashreplace')
    profile = str(network.profile)
    saved = bool(profile and network.flags & 2)
    reason = ('windows_setup_required' if not saved else
              'wifi_adhoc_unsupported' if network.bss_type != 1 else
              'wifi_network_unavailable' if not network.connectable else '')
    return dict(ssid=ssid or 'Mạng ẩn', ssid_hex=raw.hex().upper(),
                profile=profile if saved else '', saved=saved,
                signal=min(100, max(0, int(network.signal))),
                secure=bool(network.secure), connected=bool(network.flags & 1),
                connectable=not reason, reason=reason,
                setup_required=not saved, reason_code=int(network.reason_code))


def normalize_networks(rows):
    # WLAN can return an unprofiled entry alongside the saved-profile entry.
    profiled = {(row['ssid_hex'], row['secure']) for row in rows if row['saved']}
    unique = {}
    for row in rows:
        if not row['saved'] and (row['ssid_hex'], row['secure']) in profiled:
            continue
        key = (row['ssid_hex'], row['profile'], row['secure'])
        if key not in unique or row['signal'] > unique[key]['signal']:
            unique[key] = row
    return sorted(unique.values(), key=lambda row: (not row['connected'],
                  not row['saved'], -row['signal'], row['ssid'].casefold()))


class NativeWlan:
    def __init__(self):
        self.api = C.WinDLL('wlanapi', use_last_error=True)
        self.api.WlanOpenHandle.argtypes = [W.DWORD, C.c_void_p,
                                           C.POINTER(W.DWORD), C.POINTER(W.HANDLE)]
        self.api.WlanOpenHandle.restype = W.DWORD
        self.api.WlanCloseHandle.argtypes = [W.HANDLE, C.c_void_p]
        self.api.WlanCloseHandle.restype = W.DWORD
        self.api.WlanGetAvailableNetworkList.argtypes = [W.HANDLE, C.POINTER(Guid),
                   W.DWORD, C.c_void_p, C.POINTER(C.c_void_p)]
        self.api.WlanGetAvailableNetworkList.restype = W.DWORD
        self.api.WlanFreeMemory.argtypes = [C.c_void_p]
        self.api.WlanFreeMemory.restype = None

    @staticmethod
    def check(code):
        if not code:
            return
        if code == 5:
            raise OSError('wifi_permission')
        if code in (5023, 0x80342002):
            raise OSError('wifi_disabled')
        if code == 1062:
            raise OSError('wifi_service_stopped')
        raise OSError('wifi_read_failed')

    def networks(self, adapter_id):
        handle, version, data = W.HANDLE(), W.DWORD(), C.c_void_p()
        guid = Guid.from_id(adapter_id)
        self.check(self.api.WlanOpenHandle(2, None, C.byref(version), C.byref(handle)))
        try:
            # INCLUDE_ALL_MANUAL_HIDDEN_PROFILES; API owns and frees the buffer.
            self.check(self.api.WlanGetAvailableNetworkList(handle, C.byref(guid),
                       2, None, C.byref(data)))
            if not data.value:
                raise OSError('wifi_read_failed')
            count = ListHeader.from_address(data.value).count
            if count > 4096:
                raise OSError('wifi_read_failed')
            rows = []
            start = data.value + C.sizeof(ListHeader)
            for index in range(count):
                row = AvailableNetwork.from_address(start + index * C.sizeof(AvailableNetwork))
                rows.append(network_row(row))
            return normalize_networks(rows)
        finally:
            if data.value:
                self.api.WlanFreeMemory(data)
            self.api.WlanCloseHandle(handle, None)


def read_wifi_networks(adapter_id, timeout=WIFI_READ_TIMEOUT):
    """Return metadata or an actionable OSError; a stalled read cannot block UI.

    Native reads may remain in a daemon thread until Windows returns. That
    thread owns/frees its WLAN handle and performs no network mutations.
    """
    adapter_id = str(UUID(adapter_id))
    if not _read_lock.acquire(blocking=False):
        raise TimeoutError('wifi_read_timeout')
    result = queue.Queue(maxsize=1)

    def read():
        try:
            result.put((True, NativeWlan().networks(adapter_id)))
        except Exception as exc:
            result.put((False, exc))
        finally:
            _read_lock.release()

    threading.Thread(target=read, daemon=True).start()
    try:
        ok, value = result.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError('wifi_read_timeout') from None
    if not ok:
        raise value
    return value
