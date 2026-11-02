"""Collect, then run pytest in a separate bounded process. No package install.

Windows: kill-on-close Job, private-memory budget shared by <=32 processes.
POSIX: address-space/CPU limits, own process group, wall/output budgets.
The child waits for START before importing pytest or any selected test module.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))


def parallel_arguments(arguments):
    return any(arg.startswith('-n') or arg in ('--numprocesses','--dist','-d','-o','--override-ini')
        or arg.startswith(('--numprocesses=','--dist=','--override-ini='))
        or 'xdist' in arg or 'addopts' in arg for arg in arguments)


def child(memory_mib, cpu_seconds, arguments):
    if sys.stdin.buffer.read(6)!=b'START\n':return 2
    os.environ.pop('PYTEST_ADDOPTS',None)
    if parallel_arguments(arguments):return 2
    os.environ['ORT_DISABLE_TELEMETRY']='1'
    os.environ.setdefault('OMP_NUM_THREADS','2')
    os.environ.setdefault('OPENBLAS_NUM_THREADS','2')
    if sys.platform!='win32':
        import resource
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        resource.setrlimit(resource.RLIMIT_AS,(memory_mib*1024**2,)*2)
        resource.setrlimit(resource.RLIMIT_CPU,(cpu_seconds,cpu_seconds+2))
    import pytest
    return int(pytest.main(['-o','addopts=',*arguments]))


def rss_kib(process):
    """Direct child's observed RSS; never label this as a whole-tree total."""
    if sys.platform=='win32':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]
            _fields_ += [(name,ctypes.c_size_t) for name in (
                'PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
                'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.K32GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
        kernel.K32GetProcessMemoryInfo.restype=wintypes.BOOL
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000|0x0010,False,process.pid)
        if not handle:return None
        try:
            counters=Counters();counters.cb=ctypes.sizeof(counters)
            if kernel.K32GetProcessMemoryInfo(handle,ctypes.byref(counters),counters.cb):
                return counters.WorkingSetSize//1024
        finally:kernel.CloseHandle(handle)
        return None
    try:
        return int(next(line.split()[1] for line in Path(f'/proc/{process.pid}/status').read_text().splitlines() if line.startswith('VmRSS:')))
    except (OSError,StopIteration):return None


def run_phase(arguments, name, directory, memory_mib, timeout):
    from scripts.bounded_test_job import BoundedTestJob
    output=directory/(name+'.stdout.log');error=directory/(name+'.stderr.log')
    record=dict(arguments=arguments,memory_limit_mib=memory_mib,timeout_seconds=timeout,
        memory_limit_kind='Windows shared Job private allocation' if sys.platform=='win32' else 'POSIX per-process RLIMIT_AS',
        child_process_limit=32 if sys.platform=='win32' else 'process-group-owned; tests remain single-worker',
        model_lane='one pytest process, no xdist',started=False,exit_code=None,passed=False,
        timed_out=False,output_limit_exceeded=False,direct_child_reaped=False,
        peak_sampled_direct_child_rss_kib=None,rss_sampling_ms=100)
    job=BoundedTestJob(memory_mib,active_process_limit=32,job_memory_mib=memory_mib)
    process=None;start=time.monotonic();samples=[]
    def terminate_owned():
        if process is None:return
        try:
            if sys.platform=='win32':
                if job.handle is not None:job.terminate_all()
            else:
                os.killpg(process.pid,signal.SIGKILL)
        except OSError as exc:
            record['group_termination_error_type']=type(exc).__name__
        finally:
            # Job assignment/termination can fail. Never leave the direct child
            # blocked on START just because it was not successfully assigned.
            if process.poll() is None:process.kill()

    try:
        with output.open('wb') as stdout,error.open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child',str(memory_mib),str(timeout),*arguments],
                cwd=ROOT,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,start_new_session=sys.platform!='win32')
            job.assign(process.pid)  # fail closed BEFORE the child's START/pytest import
            process.stdin.write(b'START\n');process.stdin.close();record['started']=True
            while process.poll() is None:
                resident=rss_kib(process)
                if resident is not None:samples.append(resident)
                record['timed_out']=time.monotonic()-start>timeout
                record['output_limit_exceeded']=output.stat().st_size>8*1024**2 or error.stat().st_size>1024**2
                if record['timed_out'] or record['output_limit_exceeded']:
                    break
                time.sleep(.1)
            if process.poll() is None:
                terminate_owned()
            record['exit_code']=process.wait(timeout=10)
            record['direct_child_reaped']=True
            if sys.platform=='win32':
                record['job_before_cleanup']=job.accounting()
                remaining=record['job_before_cleanup']['active_processes']
                if remaining:
                    job.terminate_all()
                    end=time.monotonic()+5
                    while job.accounting()['active_processes'] and time.monotonic()<end:time.sleep(.05)
                record['job_after_cleanup']=job.accounting()
                record['owned_tree_exited']=record['job_after_cleanup']['active_processes']==0
                record['unexpected_children_at_test_exit']=remaining
            else:
                # Kill any test-owned descendants in this group's namespace.
                # /proc child enumeration may be limited; don't invent tree RSS.
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                record['owned_group_cleanup']='SIGKILL sent if the process group still existed'
            record['passed']=bool(record['exit_code']==0 and not record['timed_out'] and not record['output_limit_exceeded']
                and (sys.platform!='win32' or (record['owned_tree_exited'] and not record['unexpected_children_at_test_exit'])))
    except Exception as exc:
        record['error_type']=type(exc).__name__
    finally:
        cleanup_errors=[]
        try:
            if process is not None:
                if process.poll() is None:
                    try:terminate_owned()
                    except Exception as exc:cleanup_errors.append(type(exc).__name__)
                    # Independent fallback even when terminating the Job failed.
                    if process.poll() is None:
                        try:process.kill()
                        except Exception as exc:cleanup_errors.append(type(exc).__name__)
                try:
                    code=process.wait(timeout=10)
                    record['exit_code']=code
                    record['direct_child_reaped']=True
                except Exception as exc:cleanup_errors.append(type(exc).__name__)
                if process.stdin is not None:
                    try:process.stdin.close()
                    except (OSError,ValueError):pass
        finally:
            # Never skip closing the kill-on-close Job because wait/kill failed.
            try:job.close()
            except Exception as exc:cleanup_errors.append(type(exc).__name__)
            if process is not None and not record['direct_child_reaped']:
                try:
                    if process.poll() is None:process.kill()
                    record['exit_code']=process.wait(timeout=10)
                    record['direct_child_reaped']=True
                except Exception as exc:cleanup_errors.append(type(exc).__name__)
            if cleanup_errors:record['cleanup_error_types']=cleanup_errors
            if cleanup_errors or record.get('error_type') or record.get('group_termination_error_type'):
                record['passed']=False
            record['wall_seconds']=time.monotonic()-start
            record['peak_sampled_direct_child_rss_kib']=max(samples) if samples else None
            record['stdout_file']=output.name;record['stderr_file']=error.name
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--memory-mib',type=int,default=4096)
    parser.add_argument('--timeout',type=int,default=1800)
    parser.add_argument('pytest_args',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    os.environ.pop('PYTEST_ADDOPTS',None)
    if not 256<=args.memory_mib<=8192 or not 10<=args.timeout<=3600:parser.error('Invalid bounded test budget')
    arguments=args.pytest_args[1:] if args.pytest_args[:1]==['--'] else args.pytest_args
    if not arguments:arguments=['-q','--junitxml=test-results.xml']
    if parallel_arguments(arguments):parser.error('Parallel model lanes are unsupported')
    directory=args.report.resolve().parent;directory.mkdir(parents=True,exist_ok=True)
    report=dict(schema=1,platform=sys.platform,passed=False,phases={},implicit_pytest_addopts_cleared=True)
    def save():args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    save()
    # The real test XML must be produced by execution, not by collection.
    collection=[]
    skip=False
    for argument in arguments:
        if skip:
            skip=False
            continue
        if argument=='--junitxml':
            skip=True
            continue
        if not argument.startswith('--junitxml='):
            collection.append(argument)
    try:
        report['phases']['collection']=run_phase(['--collect-only',*collection],'pytest-collection',directory,args.memory_mib,min(args.timeout,120))
        save()
        if not report['phases']['collection']['passed']:return 1
        report['phases']['execution']=run_phase(arguments,'pytest-execution',directory,args.memory_mib,args.timeout)
        report['passed']=report['phases']['execution']['passed'];save()
        return 0 if report['passed'] else 1
    except Exception as exc:
        report['error_type']=type(exc).__name__;save();return 1


if __name__=='__main__':
    if sys.argv[1:2]==['--child']:
        raise SystemExit(child(int(sys.argv[2]),int(sys.argv[3]),sys.argv[4:]))
    raise SystemExit(main())
