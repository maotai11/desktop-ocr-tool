"""CI-only shared-budget Windows Job. Not imported by the delivered app."""
import ctypes
import sys


class BoundedTestJob:
    def __init__(self, memory_mib=256, *, active_process_limit=1, job_memory_mib=None):
        self.handle = None
        self.memory_mib = memory_mib
        self.active_process_limit = active_process_limit
        self.job_memory_mib = job_memory_mib

    def assign(self, process_id):
        if sys.platform != 'win32':
            return
        from ctypes import wintypes
        class BasicLimits(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64),
                        ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', ctypes.c_uint32),
                        ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t),
                        ('ActiveProcessLimit', ctypes.c_uint32),
                        ('Affinity', ctypes.c_size_t),
                        ('PriorityClass', ctypes.c_uint32),
                        ('SchedulingClass', ctypes.c_uint32)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IO),
                        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        job = kernel.CreateJobObjectW(None, None)  # non-inherited, unnamed
        if not job:
            raise OSError(ctypes.get_last_error(), 'CreateJobObjectW')
        process = None
        try:
            limits = ExtendedLimits()
            # Kill on parent closure; cap private native allocation and bound
            # the expected pytest subprocess tree. This is resource ownership, not a sandbox.
            limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x100 | 0x8
            limits.BasicLimitInformation.ActiveProcessLimit = self.active_process_limit
            limits.ProcessMemoryLimit = self.memory_mib * 1024 * 1024
            if self.job_memory_mib is not None:
                limits.BasicLimitInformation.LimitFlags |= 0x200
                limits.JobMemoryLimit = self.job_memory_mib * 1024 * 1024
            if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise OSError(ctypes.get_last_error(), 'SetInformationJobObject')
            process = kernel.OpenProcess(0x0100 | 0x0001, False, process_id)
            if not process or not kernel.AssignProcessToJobObject(job, process):
                raise OSError(ctypes.get_last_error(), 'AssignProcessToJobObject')
            self.handle, self._kernel = job, kernel
            self._limits_type = ExtendedLimits
        except BaseException:
            kernel.CloseHandle(job)
            raise
        finally:
            if process:
                kernel.CloseHandle(process)

    def accounting(self):
        """Owned Job allocation peaks/active count; these are not RSS values."""
        if self.handle is None:
            return {}
        from ctypes import wintypes
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in (
                'TotalUserTime', 'TotalKernelTime', 'ThisPeriodTotalUserTime', 'ThisPeriodTotalKernelTime')]
            _fields_ += [(name, ctypes.c_uint32) for name in (
                'TotalPageFaultCount', 'TotalProcesses', 'ActiveProcesses', 'TotalTerminatedProcesses')]
        query = self._kernel.QueryInformationJobObject
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        query.restype = wintypes.BOOL
        usage, limits = Accounting(), self._limits_type()
        if (not query(self.handle, 1, ctypes.byref(usage), ctypes.sizeof(usage), None)
                or not query(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits), None)):
            raise OSError(ctypes.get_last_error(), 'QueryInformationJobObject')
        return {'active_processes': usage.ActiveProcesses,
                'peak_job_private_bytes': limits.PeakJobMemoryUsed,
                'peak_process_private_bytes': limits.PeakProcessMemoryUsed}

    def terminate_all(self):
        if self.handle is not None:
            from ctypes import wintypes
            terminate = self._kernel.TerminateJobObject
            terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
            terminate.restype = wintypes.BOOL
            if not terminate(self.handle, 1):
                raise OSError(ctypes.get_last_error(), 'TerminateJobObject')

    def close(self):
        if self.handle is not None:
            self._kernel.CloseHandle(self.handle)
            self.handle = None
