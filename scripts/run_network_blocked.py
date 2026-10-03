"""Linux-only, process-local native socket deny probe; never a Windows gate.

Usage: python scripts/run_network_blocked.py --report guard.json -- command args
The command inherits seccomp through exec and threads; existing file/network
descriptors are not an offline-machine simulation. No system settings change.
"""
import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import socket
import sys


NETWORK_SYSCALLS = ('socket', 'socketpair', 'connect', 'bind', 'listen', 'accept',
                    'accept4', 'sendto', 'sendmsg', 'sendmmsg', 'recvfrom',
                    'recvmsg', 'recvmmsg', 'shutdown')


def install_guard():
    if sys.platform != 'linux':
        raise RuntimeError('This probe requires Linux and libseccomp; no Windows claim')
    lib = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                         ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
    lib.seccomp_rule_add_array.restype = ctypes.c_int
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_load.restype = ctypes.c_int
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError('seccomp_init failed')
    loaded = []
    try:
        for name in NETWORK_SYSCALLS:
            number = lib.seccomp_syscall_resolve_name(name.encode('ascii'))
            if number < 0:
                raise RuntimeError(f'Cannot resolve network syscall: {name}')
            code = lib.seccomp_rule_add_array(context, 0x00050000 | errno.EPERM,
                                              number, 0, None)
            if code != 0:
                raise RuntimeError(f'Cannot deny {name}: {code}')
            loaded.append(name)
        code = lib.seccomp_load(context)
        if code != 0:
            raise RuntimeError(f'seccomp_load failed: {code}')
    finally:
        lib.seccomp_release(context)
    # Prove the loaded filter affects this process before launching inference.
    checks = {}
    for name, family in [('ipv4', socket.AF_INET), ('ipv6', socket.AF_INET6),
                         ('unix', socket.AF_UNIX)]:
        try:
            with socket.socket(family, socket.SOCK_STREAM):
                pass
        except PermissionError as exc:
            if exc.errno != errno.EPERM:
                raise
            checks[name] = 'EPERM'
        else:
            raise RuntimeError(f'{name} socket unexpectedly allowed')
    return {'schema': 1, 'platform': sys.platform, 'filter_loaded': True,
            'denied_syscalls': loaded, 'socket_checks': checks,
            'limits': ['Process-local Linux test, not Windows/offline-machine acceptance',
                       'No packet capture or proof of absent historical payload',
                       'Inherited descriptors are not closed by this filter']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('A command is required after --')
    report = install_guard()
    report['command'] = command
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    os.execvp(command[0], command)


if __name__ == '__main__':
    main()
