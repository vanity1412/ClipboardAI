"""Explicit physical-adapter and saved Wi-Fi selection with cooperative rollback."""
import base64
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from uuid import UUID

from wifi_networks import read_wifi_networks


HELPER_TIMEOUT = 45
ROLLBACK_GRACE = 15
_switch_lock = threading.Lock()
_switch_pending = threading.Event()
_last_switch_outcome = None


def network_recovery_pending():
    """A timed-out helper must finish recovery before another operation starts."""
    return _switch_pending.is_set()


class DoublePress:
    def __init__(self, interval=.5):
        self.interval = interval
        self.previous = None

    def press(self, now):
        if self.previous is not None and 0 <= now - self.previous <= self.interval:
            self.previous = None
            return True
        self.previous = now
        return False


WLAN_HELPER_SOURCE = r"""
using System;
using System.Runtime.InteropServices;
public static class ClipboardWifi {
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    struct Parameters { public int Mode; [MarshalAs(UnmanagedType.LPWStr)] public string Profile;
        public IntPtr Ssid; public IntPtr Bssids; public int BssType; public uint Flags; }
    [StructLayout(LayoutKind.Sequential)]
    struct Ssid { public uint Length; [MarshalAs(UnmanagedType.ByValArray, SizeConst=32)] public byte[] Bytes; }
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    struct Connection { public int State; public int Mode; [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)] public string Profile; public Ssid Ssid; }
    [DllImport("wlanapi.dll")] static extern uint WlanOpenHandle(uint v, IntPtr r, out uint n, out IntPtr h);
    [DllImport("wlanapi.dll")] static extern uint WlanCloseHandle(IntPtr h, IntPtr r);
    [DllImport("wlanapi.dll")] static extern uint WlanConnect(IntPtr h, ref Guid g, ref Parameters p, IntPtr r);
    [DllImport("wlanapi.dll")] static extern uint WlanDisconnect(IntPtr h, ref Guid g, IntPtr r);
    [DllImport("wlanapi.dll")] static extern uint WlanQueryInterface(IntPtr h, ref Guid g, int op, IntPtr r, out uint size, out IntPtr data, out int kind);
    [DllImport("wlanapi.dll")] static extern void WlanFreeMemory(IntPtr p);
    public static void Connect(Guid g, string profile) {
        uint version; IntPtr h; if (WlanOpenHandle(2, IntPtr.Zero, out version, out h) != 0) throw new Exception("wifi_unavailable");
        try { Parameters p = new Parameters { Mode=0, Profile=profile, BssType=1 };
            if (WlanConnect(h, ref g, ref p, IntPtr.Zero) != 0) throw new Exception("wifi_connect_failed");
        } finally { WlanCloseHandle(h, IntPtr.Zero); }
    }
    public static string[] Current(Guid g) {
        uint version; IntPtr h; if (WlanOpenHandle(2, IntPtr.Zero, out version, out h) != 0) throw new Exception("wifi_unavailable");
        IntPtr data=IntPtr.Zero;
        try { uint size; int kind;
            uint code=WlanQueryInterface(h, ref g, 7, IntPtr.Zero, out size, out data, out kind);
            if (code == 5023) return new string[] {"", ""};
            if (code != 0) throw new Exception("wifi_unavailable");
            Connection c=(Connection)Marshal.PtrToStructure(data, typeof(Connection));
            if (c.State != 1) return new string[] {"", ""};
            return new string[] {c.Profile, BitConverter.ToString(c.Ssid.Bytes, 0, (int)Math.Min(c.Ssid.Length, 32)).Replace("-", "")};
        } finally { if (data != IntPtr.Zero) WlanFreeMemory(data); WlanCloseHandle(h, IntPtr.Zero); }
    }
    public static void Disconnect(Guid g) {
        uint version; IntPtr h; if (WlanOpenHandle(2, IntPtr.Zero, out version, out h) != 0) throw new Exception("wifi_unavailable");
        try { if (WlanDisconnect(h, ref g, IntPtr.Zero) != 0) throw new Exception("wifi_connect_failed"); }
        finally { WlanCloseHandle(h, IntPtr.Zero); }
    }
}
"""


# These definitions also run under mocked cmdlets in the transaction tests.
POWERSHELL_FUNCTIONS = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
function Read-PhysicalAdapters {
    @(Get-NetAdapter -Physical | ForEach-Object {
        $kind = ''
        if ($_.InterfaceType -eq 71 -or $_.NdisPhysicalMedium -in @(1,9)) { $kind = 'wifi' }
        elseif ($_.InterfaceType -eq 6 -and $_.NdisPhysicalMedium -in @(0,14) -and $_.InterfaceDescription -notmatch 'Bluetooth') { $kind = 'lan' }
        if ($kind -and $_.HardwareInterface -and -not $_.Virtual) {
            [pscustomobject]@{id=([guid]$_.InterfaceGuid).ToString(); name=$_.Name;
                description=$_.InterfaceDescription; kind=$kind; status=[string]$_.Status;
                enabled=([int]$_.AdminStatus -eq 1); index=[int]$_.ifIndex}
        }
    })
}
function Find-PhysicalAdapter($id) {
    $found = @(Get-NetAdapter -Physical | Where-Object { ([guid]$_.InterfaceGuid).ToString() -eq $id })
    if ($found.Count -ne 1) { throw 'adapter_missing' }
    $found[0]
}
function Test-AdapterReady($id) {
    $nic = Find-PhysicalAdapter $id
    if ([string]$nic.Status -ne 'Up') { return $false }
    $ip = Get-NetIPConfiguration -InterfaceIndex $nic.ifIndex
    $v4 = @($ip.IPv4Address | Where-Object { $_.IPAddress -and $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '0.0.0.0' })
    $v6 = @($ip.IPv6Address | Where-Object { $_.IPAddress -and $_.IPAddress -notlike 'fe80:*' -and $_.IPAddress -ne '::1' })
    $gateway4 = @($ip.IPv4DefaultGateway | Where-Object { $_.NextHop })
    $gateway6 = @($ip.IPv6DefaultGateway | Where-Object { $_.NextHop })
    return (($v4.Count -gt 0 -and $gateway4.Count -gt 0) -or ($v6.Count -gt 0 -and $gateway6.Count -gt 0))
}
function Test-ProviderConnection($hostName) {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync($hostName, 443)
        return ($task.Wait(4000) -and $client.Connected)
    } catch { return $false }
    finally { $client.Dispose() }
}
function Test-GroupReady($adapters) {
    foreach ($adapter in $adapters) {
        if (Test-AdapterReady $adapter.id) { return $true }
    }
    return $false
}
function Test-SwitchDeadline($request, $deadline) {
    if (($request.cancel_path -and [IO.File]::Exists($request.cancel_path)) -or [DateTime]::UtcNow -ge $deadline) { throw 'switch_timeout' }
}
function Initialize-WlanHelper {
    if ('ClipboardWifi' -as [type]) { return }
    Add-Type -TypeDefinition $script:WlanHelperSource
}
function Connect-WifiProfile($id, $profile) {
    Initialize-WlanHelper
    [ClipboardWifi]::Connect([guid]$id, $profile)
}
function Read-WifiConnection($id) {
    Initialize-WlanHelper
    $current = [ClipboardWifi]::Current([guid]$id)
    return @{profile=$current[0]; ssid_hex=$current[1]}
}
function Disconnect-WifiProfile($id) {
    Initialize-WlanHelper
    [ClipboardWifi]::Disconnect([guid]$id)
}
function Invoke-NetworkSwitch($request) {
    $initial = @(Read-PhysicalAdapters)
    if ($request.target_mode -notin @('wifi','lan')) { throw 'adapter_missing' }
    $targets = @($initial | Where-Object { $_.kind -eq $request.target_mode -and (-not $request.adapter_id -or $_.id -eq $request.adapter_id) })
    $sources = @($initial | Where-Object { $_.id -notin @($targets.id) })
    if ($targets.Count -eq 0) { throw 'adapter_missing' }
    $targetTouched = [Collections.Generic.List[string]]::new()
    $sourceTouched = [Collections.Generic.List[string]]::new()
    $wifiBefore = $null
    $wifiTouched = $false
    $seconds = if ($request.timeout_seconds) { [Math]::Min(35, [Math]::Max(1, [int]$request.timeout_seconds)) } else { 35 }
    $operationDeadline = [DateTime]::UtcNow.AddSeconds($seconds)
    try {
        Test-SwitchDeadline $request $operationDeadline
        if ($request.wifi_profile) {
            if ($request.target_mode -ne 'wifi' -or $targets.Count -ne 1) { throw 'adapter_missing' }
            if ($targets[0].enabled) { $wifiBefore = Read-WifiConnection $targets[0].id }
        }
        foreach ($target in $targets) {
            Test-SwitchDeadline $request $operationDeadline
            if (-not $target.enabled) {
                $targetTouched.Add($target.id)
                Find-PhysicalAdapter $target.id | Enable-NetAdapter -Confirm:$false
            }
        }
        if ($request.wifi_profile) {
            Test-SwitchDeadline $request $operationDeadline
            $wifiTouched = $true
            Connect-WifiProfile $targets[0].id $request.wifi_profile
        }
        $deadline = [DateTime]::UtcNow.AddSeconds($(if ($request.wifi_profile) { 25 } else { 15 }))
        while ($true) {
            Test-SwitchDeadline $request $operationDeadline
            $wifiReady = $true
            if ($request.wifi_profile) {
                $current = Read-WifiConnection $targets[0].id
                $wifiReady = ($current.profile -ceq $request.wifi_profile -and (-not $request.ssid_hex -or $current.ssid_hex -eq $request.ssid_hex))
            }
            if ($wifiReady -and (Test-GroupReady $targets)) { break }
            if ([DateTime]::UtcNow -ge $deadline) { throw 'target_unavailable' }
            Start-Sleep -Milliseconds 250
        }
        foreach ($source in $sources) {
            Test-SwitchDeadline $request $operationDeadline
            if ($source.enabled) {
                $sourceTouched.Add($source.id)
                Find-PhysicalAdapter $source.id | Disable-NetAdapter -Confirm:$false
            }
        }
        Test-SwitchDeadline $request $operationDeadline
        if (-not (Test-GroupReady $targets) -or -not (Test-ProviderConnection $request.probe_host)) { throw 'provider_unreachable' }
        $final = @(Read-PhysicalAdapters)
        if (@($final | Where-Object { $_.id -notin @($targets.id) -and $_.enabled }).Count) { throw 'disable_failed' }
        foreach ($target in $targets) {
            $after = @($final | Where-Object { $_.id -eq $target.id })
            if ($after.Count -ne 1 -or -not $after[0].enabled) { throw 'enable_failed' }
        }
        return @{ok=$true; code='switched'; mode=$request.target_mode; adapters=$final}
    } catch {
        $failure = $_.Exception.Message
        $rollbackOk = $true
        # Restore the old network first; never disable both as the transition.
        foreach ($id in $sourceTouched) {
            try {
                Find-PhysicalAdapter $id | Enable-NetAdapter -Confirm:$false
                if ([int](Find-PhysicalAdapter $id).AdminStatus -ne 1) { throw 'rollback_failed' }
            } catch { $rollbackOk = $false }
        }
        if ($wifiTouched -and $wifiBefore -and $wifiBefore.profile) {
            try {
                Connect-WifiProfile $targets[0].id $wifiBefore.profile
                $restoreDeadline = [DateTime]::UtcNow.AddSeconds(10)
                while ($true) {
                    $restoredWifi = Read-WifiConnection $targets[0].id
                    if ($restoredWifi.profile -ceq $wifiBefore.profile -and (Test-AdapterReady $targets[0].id)) { break }
                    if ([DateTime]::UtcNow -ge $restoreDeadline) { throw 'rollback_failed' }
                    Start-Sleep -Milliseconds 250
                }
            } catch { $rollbackOk = $false }
        }
        elseif ($wifiTouched -and $wifiBefore) {
            try {
                Disconnect-WifiProfile $targets[0].id
                $restoreDeadline = [DateTime]::UtcNow.AddSeconds(5)
                while ((Read-WifiConnection $targets[0].id).profile) {
                    if ([DateTime]::UtcNow -ge $restoreDeadline) { throw 'rollback_failed' }
                    Start-Sleep -Milliseconds 250
                }
            } catch { $rollbackOk = $false }
        }
        # Administrative enable alone does not establish Wi-Fi/DHCP. Preserve
        # the new card if an originally connected old card has not recovered.
        foreach ($id in $sourceTouched) {
            $before = @($initial | Where-Object { $_.id -eq $id })[0]
            if ($before.status -eq 'Up') {
                try {
                    $restoreDeadline = [DateTime]::UtcNow.AddSeconds(10)
                    while (-not (Test-AdapterReady $id)) {
                        if ([DateTime]::UtcNow -ge $restoreDeadline) { throw 'rollback_failed' }
                        Start-Sleep -Milliseconds 250
                    }
                } catch { $rollbackOk = $false }
            }
        }
        if ($rollbackOk) {
            foreach ($id in $targetTouched) {
                try { Find-PhysicalAdapter $id | Disable-NetAdapter -Confirm:$false } catch { $rollbackOk = $false }
            }
        }
        $restored = @(Read-PhysicalAdapters)
        foreach ($before in $initial) {
            $after = @($restored | Where-Object { $_.id -eq $before.id })
            if ($after.Count -ne 1 -or $after[0].enabled -ne $before.enabled) { $rollbackOk = $false }
        }
        $known = @('target_unavailable','provider_unreachable','disable_failed','enable_failed','adapter_missing','switch_timeout','wifi_connect_failed','wifi_unavailable')
        $code = if (-not $rollbackOk) { 'rollback_failed' } elseif ($failure -in $known) { $failure } else { 'switch_failed' }
        return @{ok=$false; code=$code; adapters=$restored}
    }
}
function Invoke-WifiPriority($request) {
    $result = Invoke-NetworkSwitch $request
    if ($result.ok -or $result.code -notin @('target_unavailable','provider_unreachable')) { return $result }
    # LAN might have been disabled by the previous Wi-Fi session. Try it if
    # Wi-Fi is unavailable, within this same elevated transaction/UAC prompt.
    $fallback = [pscustomobject]@{target_mode='lan'; probe_host=$request.probe_host}
    $result = Invoke-NetworkSwitch $fallback
    if ($result.ok) { $result.code = 'fallback_lan' }
    return $result
}
"""


def encoded_command(script):
    return base64.b64encode(script.encode('utf-16-le')).decode('ascii')


def powershell_path():
    return str(Path(os.environ.get('SystemRoot', r'C:\Windows')) /
               'System32/WindowsPowerShell/v1.0/powershell.exe')


def read_adapters():
    command = POWERSHELL_FUNCTIONS + '\nConvertTo-Json -InputObject @(Read-PhysicalAdapters) -Depth 4 -Compress'
    result = subprocess.run([powershell_path(), '-NoProfile', '-NonInteractive', '-EncodedCommand',
                             encoded_command(command)], capture_output=True, timeout=20,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise OSError('adapter_read_failed')
    data = json.loads(result.stdout.decode('utf-8-sig'))
    if not isinstance(data, list):
        raise ValueError('adapter_read_failed')
    for row in data:
        row['id'] = str(UUID(row['id']))
        if row.get('kind') not in ('wifi', 'lan') or type(row.get('enabled')) is not bool:
            raise ValueError('adapter_read_failed')
    return data


def make_switch_script(target_mode, probe_host, result_path, prefer_wifi=False, adapter_id=None,
                       wifi_profile=None, ssid_hex=None, cancel_path=None):
    # JSON data is base64 encoded, never interpolated as executable PowerShell.
    if target_mode not in ('wifi', 'lan'):
        raise ValueError('adapter_missing')
    request = dict(target_mode=target_mode, probe_host=probe_host,
                   result_path=str(result_path), prefer_wifi=bool(prefer_wifi))
    if adapter_id is not None:
        request['adapter_id'] = str(UUID(adapter_id))
    if wifi_profile is not None:
        if target_mode != 'wifi' or not isinstance(wifi_profile, str) or not wifi_profile or len(wifi_profile) > 255 or '\0' in wifi_profile:
            raise ValueError('wifi_profile_missing')
        request['wifi_profile'] = wifi_profile
        request['wifi_helper'] = WLAN_HELPER_SOURCE
    if ssid_hex is not None:
        if not isinstance(ssid_hex, str) or len(ssid_hex) > 64 or len(ssid_hex) % 2:
            raise ValueError('wifi_network_missing')
        try:
            bytes.fromhex(ssid_hex)
        except ValueError:
            raise ValueError('wifi_network_missing') from None
        request['ssid_hex'] = ssid_hex.upper()
    if cancel_path is not None:
        request['cancel_path'] = str(cancel_path)
    request['timeout_seconds'] = 35
    payload = base64.b64encode(json.dumps(request, ensure_ascii=True).encode('utf-8')).decode('ascii')
    return POWERSHELL_FUNCTIONS + "\n$request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + payload + "')) | ConvertFrom-Json\n" + r"""
$script:WlanHelperSource = $request.wifi_helper
try {
    $result = if ($request.prefer_wifi) { Invoke-WifiPriority $request } else { Invoke-NetworkSwitch $request }
}
catch { $result = @{ok=$false; code='adapter_missing'; adapters=@()} }
$text = ConvertTo-Json -InputObject $result -Depth 5 -Compress
[IO.File]::WriteAllText($request.result_path, $text, [Text.UTF8Encoding]::new($false))
"""


def _execute_switch(target_mode, probe_host, prefer_wifi, adapter_id, wifi_profile,
                    ssid_hex, result_path, cancel_path):
    # Keep the result file owned by the unelevated app. The elevated helper
    # writes into it instead of creating a file the app may not read.
    result_path.write_text('', encoding='utf-8')
    script = make_switch_script(target_mode, probe_host, result_path, prefer_wifi,
                                adapter_id, wifi_profile, ssid_hex, cancel_path)
    script_path = result_path.with_suffix('.ps1')
    script_path.write_text(script, encoding='utf-8-sig')
    arguments = ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script_path)]
    if C.windll.shell32.IsUserAnAdmin():
        result = subprocess.run([powershell_path()] + arguments,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise OSError('switch_failed')
    else:
        # Only this fixed adapter transaction is elevated; main app stays normal.
        class ExecuteInfo(C.Structure):
            _fields_ = [('cbSize', W.DWORD), ('fMask', W.ULONG), ('hwnd', W.HWND),
                        ('lpVerb', W.LPCWSTR), ('lpFile', W.LPCWSTR), ('lpParameters', W.LPCWSTR),
                        ('lpDirectory', W.LPCWSTR), ('nShow', C.c_int), ('hInstApp', W.HINSTANCE),
                        ('lpIDList', C.c_void_p), ('lpClass', W.LPCWSTR), ('hkeyClass', W.HKEY),
                        ('dwHotKey', W.DWORD), ('hIcon', W.HANDLE), ('hProcess', W.HANDLE)]
        shell = C.WinDLL('shell32', use_last_error=True)
        kernel = C.WinDLL('kernel32', use_last_error=True)
        shell.ShellExecuteExW.argtypes, shell.ShellExecuteExW.restype = [C.POINTER(ExecuteInfo)], W.BOOL
        kernel.WaitForSingleObject.argtypes, kernel.WaitForSingleObject.restype = [W.HANDLE, W.DWORD], W.DWORD
        kernel.CloseHandle.argtypes = [W.HANDLE]
        info = ExecuteInfo()
        info.cbSize, info.fMask, info.lpVerb = C.sizeof(info), 0x40 | 0x400, 'runas'
        info.lpFile, info.lpParameters, info.nShow = powershell_path(), subprocess.list2cmdline(arguments), 0
        if not shell.ShellExecuteExW(C.byref(info)):
            raise PermissionError('uac_denied' if C.get_last_error() == 1223 else 'admin_required')
        try:
            # Blocking UAC/process waits are confined to the network thread.
            if kernel.WaitForSingleObject(info.hProcess, 0xFFFFFFFF) != 0:
                raise OSError('switch_failed')
        finally:
            kernel.CloseHandle(info.hProcess)
    if not result_path.exists() or result_path.stat().st_size == 0:
        raise OSError('switch_failed')
    return json.loads(result_path.read_text(encoding='utf-8-sig'))


def run_switch(target_mode, probe_host, prefer_wifi=False, adapter_id=None,
               wifi_profile=None, ssid_hex=None):
    """Bound the caller's wait without terminating a transaction during rollback.

    On timeout a cancellation file asks the helper to roll back between steps.
    If Windows/UAC/driver is still blocked after the grace period, retain all
    temporary resources and mark recovery pending until its worker finishes.
    """
    if not _switch_lock.acquire(blocking=False):
        return dict(ok=False, code='switch_recovery_pending', recovery_pending=True)
    try:
        folder = tempfile.TemporaryDirectory(prefix='ClipboardAI_Network_')
        result_path = Path(folder.name) / 'result.json'
        cancel_path = Path(folder.name) / 'cancel'
        result_path.write_text('', encoding='utf-8')
    except Exception:
        _switch_lock.release()
        raise
    completed = threading.Event()
    outcome = []
    _switch_pending.set()

    def work():
        global _last_switch_outcome
        try:
            outcome.append(_execute_switch(target_mode, probe_host, prefer_wifi,
                       adapter_id, wifi_profile, ssid_hex, result_path, cancel_path))
        except Exception as exc:
            outcome.append(exc)
        finally:
            _last_switch_outcome = outcome[0]
            # Ownership stays with the worker if the bounded caller returns.
            try:
                folder.cleanup()
            except OSError:
                pass
            finally:
                _switch_pending.clear()
                _switch_lock.release()
                completed.set()

    threading.Thread(target=work, daemon=True).start()
    if not completed.wait(HELPER_TIMEOUT):
        try:
            cancel_path.write_text('cancel', encoding='ascii')
        except OSError:
            # It may have finished and cleaned its folder at the wait boundary.
            if not completed.is_set():
                return dict(ok=False, code='switch_recovery_pending', recovery_pending=True)
        if not completed.wait(ROLLBACK_GRACE):
            return dict(ok=False, code='switch_recovery_pending', recovery_pending=True)
    result = outcome[0]
    if isinstance(result, Exception):
        raise result
    return result


NETWORK_MESSAGES = {
    'adapter_read_failed': 'Không đọc được card mạng; thử Làm mới mạng trong tray.',
    'adapter_missing': 'Không tìm thấy card vật lý thuộc nhóm mạng đích; làm mới mạng trong tray.',
    'target_unavailable': 'Mạng đích chưa kết nối/có IP; giữ trạng thái mạng trước.',
    'provider_unreachable': 'Mạng đích chưa kết nối được API; đã khôi phục mạng trước.',
    'disable_failed': 'Không tắt được card cũ; đã khôi phục mạng trước.',
    'enable_failed': 'Không bật được đủ card mạng đích; đã khôi phục mạng trước.',
    'switch_failed': 'Không chuyển được mạng; đã thử khôi phục trạng thái trước.',
    'rollback_failed': 'Không khôi phục đủ card mạng; kiểm tra Wi-Fi/LAN trong Windows.',
    'uac_denied': 'Bạn đã đóng UAC; chưa chuyển mạng.',
    'admin_required': 'Windows cần quyền admin để bật/tắt card mạng.',
    'choose_adapter': 'Chọn Wi-Fi hoặc LAN, rồi chọn card muốn dùng trong menu Mạng.',
    'switch_timeout': 'Chuyển mạng quá thời gian; đã khôi phục trạng thái trước.',
    'switch_recovery_pending': 'Windows đang hoàn tất/khôi phục thao tác mạng; chưa đổi card tiếp. Có thể đọc mạng hoặc thử gửi AI.',
    'wifi_disabled': 'Card Wi-Fi đang tắt/radio tắt; bật Wi-Fi trong Windows rồi đọc danh sách mạng.',
    'wifi_permission': 'Windows chưa cho đọc Wi-Fi; bật quyền Vị trí cho ứng dụng desktop trong Cài đặt Windows rồi thử lại.',
    'wifi_service_stopped': 'Dịch vụ WLAN AutoConfig chưa chạy; kiểm tra Wi-Fi trong Windows.',
    'wifi_read_failed': 'Không đọc được danh sách Wi-Fi; mở thiết lập Wi-Fi trong Windows hoặc thử lại.',
    'wifi_read_timeout': 'Windows đọc Wi-Fi quá lâu; thử lại hoặc mở thiết lập Wi-Fi trong Windows.',
    'wifi_profile_missing': 'Chưa lưu mạng Wi-Fi này; kết nối và lưu mạng trong Windows trước.',
    'windows_setup_required': 'Chưa lưu · thiết lập trong Windows; app chỉ kết nối mạng Wi-Fi đã lưu.',
    'wifi_network_missing': 'Mạng Wi-Fi đã thay đổi/không còn trong danh sách; đọc lại danh sách Wi-Fi.',
    'wifi_network_unavailable': 'Mạng Wi-Fi hiện chưa kết nối được; thử lại hoặc kiểm tra trong Windows.',
    'wifi_adhoc_unsupported': 'Mạng Wi-Fi này cần kết nối bằng Windows.',
    'wifi_connect_failed': 'Không kết nối được Wi-Fi đã chọn; đã thử khôi phục mạng trước.',
    'wifi_unavailable': 'Windows chưa cho thao tác Wi-Fi; kiểm tra quyền Vị trí/dịch vụ Wi-Fi trong Windows.',
}


class NetworkManager:
    def __init__(self, root):
        self.path = Path(root) / 'network_config.json'
        self.prefer_wifi = False
        self.startup_delay = 30
        self.adapters = []
        self.mode = ''
        self.wifi_networks = []
        self._recovery_seen = False
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            delay = data.get('startup_delay', 30)
            if type(delay) is int and delay in (30, 60, 120):
                self.startup_delay = delay
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(prefer_wifi=self.prefer_wifi, startup_delay=self.startup_delay, adapter_policy='all_physical')), encoding='utf-8')
        temporary.replace(self.path)

    def label(self):
        if not self.adapters:
            return 'Chưa đọc card mạng'
        active = {row['kind'] for row in self.adapters if row['enabled'] and row['status'] == 'Up'}
        return 'Wi-Fi + LAN' if len(active) == 2 else 'Wi-Fi' if active == {'wifi'} else 'LAN' if active == {'lan'} else 'Chưa kết nối'

    @property
    def recovery_pending(self):
        return network_recovery_pending()

    def perform(self, target='toggle', probe_host='api.deepseek.com', adapter_id=None,
                profile_name=None, ssid=None):
        try:
            pending = self.recovery_pending
            if pending:
                self._recovery_seen = True
            if pending and target not in ('refresh', 'startup', 'wifi_scan'):
                return dict(ok=False, code='switch_recovery_pending', recovery_pending=True,
                            message=NETWORK_MESSAGES['switch_recovery_pending'])
            self.adapters = read_adapters()
            if target in ('refresh', 'startup'):
                if pending:
                    return dict(ok=True, code='switch_recovery_pending', recovery_pending=True,
                                message='Mạng: ' + self.label() + ' · Windows vẫn đang hoàn tất/khôi phục thao tác trước')
                if self._recovery_seen:
                    self._recovery_seen = False
                    outcome = _last_switch_outcome
                    code = outcome.get('code', 'switch_failed') if isinstance(outcome, dict) else 'switch_failed'
                    ok = isinstance(outcome, dict) and outcome.get('ok', False)
                    detail = 'Đã hoàn tất thao tác mạng trước' if ok else NETWORK_MESSAGES.get(code, NETWORK_MESSAGES['switch_failed'])
                    return dict(ok=True, code='recovery_finished', recovery_finished=True,
                                operation_ok=bool(ok), operation_code=code,
                                message=detail + ' · Mạng: ' + self.label())
                return {'ok': True, 'message': 'Mạng: ' + self.label()}
            groups = {kind: [row for row in self.adapters if row['kind'] == kind] for kind in ('wifi', 'lan')}
            if target in ('wifi_scan', 'wifi_connect'):
                if adapter_id is None:
                    raise ValueError('choose_adapter')
                adapter_id = str(UUID(adapter_id))
                selected = next((row for row in groups['wifi'] if row['id'] == adapter_id), None)
                if selected is None:
                    raise ValueError('adapter_missing')
                if not selected['enabled']:
                    raise ValueError('wifi_disabled')
                self.wifi_networks = read_wifi_networks(adapter_id)
                if target == 'wifi_scan':
                    return dict(ok=True, code='wifi_scanned', message='Chọn mạng Wi-Fi đã lưu',
                                adapter_id=adapter_id, networks=self.wifi_networks)
                if not isinstance(profile_name, str) or not profile_name:
                    raise ValueError('windows_setup_required')
                network = next((row for row in self.wifi_networks if row['profile'] == profile_name and (ssid is None or row['ssid'] == ssid)), None)
                if network is None:
                    raise ValueError('wifi_network_missing')
                if not network['connectable']:
                    raise ValueError(network['reason'] or 'wifi_network_unavailable')
                result = run_switch('wifi', probe_host, adapter_id=adapter_id,
                                   wifi_profile=profile_name, ssid_hex=network['ssid_hex'])
                self.adapters = result.get('adapters', self.adapters)
                if result.get('ok'):
                    self.mode = 'wifi'
                    return dict(ok=True, code='wifi_connected', ssid=network['ssid'],
                                message='Đã kết nối Wi-Fi · ' + network['ssid'])
                code = result.get('code', 'switch_failed')
                self._recovery_seen = bool(result.get('recovery_pending'))
                return dict(ok=False, code=code, recovery_pending=result.get('recovery_pending', False),
                            message=NETWORK_MESSAGES.get(code, NETWORK_MESSAGES['switch_failed']))
            if target == 'toggle':
                raise ValueError('choose_adapter')
            if target not in groups or not groups[target]:
                raise ValueError('adapter_missing')
            if adapter_id is None:
                if len(groups[target]) != 1:
                    raise ValueError('choose_adapter')
                adapter_id = groups[target][0]['id']
            adapter_id = str(UUID(adapter_id))
            selected = next((row for row in groups[target] if row['id'] == adapter_id), None)
            if selected is None:
                raise ValueError('adapter_missing')
            if selected['enabled'] and selected['status'] == 'Up' and not any(row['enabled'] for row in self.adapters if row['id'] != adapter_id):
                self.mode = target
                return {'ok': True, 'message': 'Đang dùng ' + ('Wi-Fi' if target == 'wifi' else 'LAN')}
            result = run_switch(target, probe_host, adapter_id=adapter_id)
            self.adapters = result.get('adapters', self.adapters)
            if result.get('ok'):
                self.mode = result.get('mode', target)
                if result.get('code') in ('kept_lan', 'fallback_lan'):
                    return {'ok': True, 'message': 'Wi-Fi chưa dùng được; đang dùng LAN'}
                return {'ok': True, 'message': 'Đã chuyển sang ' + ('Wi-Fi' if target == 'wifi' else 'LAN')}
            self._recovery_seen = bool(result.get('recovery_pending'))
            return {'ok': False, 'code': result.get('code', 'switch_failed'),
                    'recovery_pending': result.get('recovery_pending', False),
                    'message': NETWORK_MESSAGES.get(result.get('code'), NETWORK_MESSAGES['switch_failed'])}
        except Exception as exc:
            code = str(exc) if isinstance(exc, (OSError, ValueError)) else 'switch_failed'
            return {'ok': False, 'code': code,
                    'message': NETWORK_MESSAGES.get(code, NETWORK_MESSAGES['adapter_read_failed'])}
