"""
A Machine combines Network objects, Drives, and other parameters into a running
qemu instance, providing hooks for snapshots. Its __enter__ and __exit__ methods
allow it to be used in a with block, and can be useful when provided as a
fixture to pytest tests
"""


import os
import socket
import subprocess
import tempfile
from multiprocessing import Process
import time
import signal
from pexpect import fdpexpect
import pexpect

import backoff

# import qmp

from . import Network


@backoff.on_predicate(backoff.constant,
                      interval=1,
                      max_time=5)
def get_pid_of_qemu(pid_file):
    """
    Keeps trying to read a pidfile, and returns its contents
    """
    with open(pid_file, 'r') as f:
        val = f.readline().strip()
        return int(val) if val is not "" else None


class Machine:
    """
    The base machine class, taking many of the same parameters that
    qemu-system-x86_64 takes, but some more complex ones as python objects
    """

    def __init__(self, cpus=1, mem=1024, drives=[], net=[], kernel=None, kernel_append=None, boot_order=None, extra_serials=0, ephemeral=True, qemu_command_line=[]):
        self.net = net
        self.cpus = cpus
        self.mem = mem
        self.drives = drives
        self.__bridges = []
        self.__qmp_socket = None
        self.__qmp_expect = None
        self.__qmp_sock_file = None
        self.__tmp_dir = tempfile.mkdtemp()
        self.kernel = kernel
        self.kernel_append = kernel_append
        self.__boot_order = boot_order
        self.__num_extra_serials = extra_serials
        self.__serials = []
        self.ephemeral = ephemeral
        self.qemu_command_line = qemu_command_line

        for index in range(extra_serials + 1):
            serial_object = {"socket_file": os.path.join(self.__tmp_dir, "serial_{}.sock".format(index)),
                             "socket": None, "expect": None, "logfd": None, "logfile": os.path.join(self.__tmp_dir, "serial_{}.log".format(index))}
            serial_object["logfd"] = open(serial_object["logfile"], mode='w')
            self.__serials += [serial_object]

    def cleanup(self):
        os.system("rm -rf {}".format(self.__tmp_dir))

    @property
    def qmp_socket(self):
        return self.__qmp_socket

    @property
    def serial_socket(self):
        return self.__serials[0]["socket"]

    @property
    def serial_expect(self):
        return self.__serials[0]["expect"]

    @property
    def qmp_expect(self):
        return self.__qmp_expect

    def stop(self):
        """
        On exit, send a SIGINT to the qemu process, and hope it dies quickly.
        Won't work if it's locked up for any reason
        """

        # XXX FIXME - don't rely on timing
        # self.__qmp_socket.send("quit\n".encode())
        # time.sleep(1)

        self.kill_qemu()
        for n in self.net:
            print("Deleting bridge ", n.uid)
            n.delete_bridge()

        for serial_object in self.__serials:
            try:
                serial_object["expect"].close()
            except OSError:
                pass
            serial_object["expect"] = None

    def start(self):
        """
        Called on __enter__, or manually to start the VM and connect sockets
        """

        cpu_strings = ["-smp", str(self.cpus)]

        mem_strings = ["-m", str(self.mem)]

        drive_strings = []
        for d in self.drives:
            if "preboot" in d.snapshots:
                os.system(
                    "qemu-img snapshot -a preboot {}".format(d.backing_file))
            else:
                os.system(
                    "qemu-img snapshot -c preboot {}".format(d.backing_file))
            if self.ephemeral:
                d.create_disk_layer(self.__tmp_dir)
            drive_strings += ["-drive", d.to_drive_string()]

        net_strings = []
        for n in self.net:
            br_name = n.create_bridge()
            self.__bridges += [br_name]
            # last 6 hex chars of mac_addr
            br_id = n.mac_addr.replace(":", "")[-6:]
            net_strings += ["-netdev", "bridge,id={},br={}".format(
                br_name, br_name), "-device", "e1000,netdev={},mac={}".format(br_name, n.mac_addr)]

        fd, self.pidfile = tempfile.mkstemp(
            suffix=".pid", prefix="pidfile_", dir=self.__tmp_dir)
        os.close(fd)
        pid_strings = ["-pidfile", self.pidfile]

        kernel_strings = []
        if self.kernel is not None:
            kernel_strings = ["-kernel", self.kernel]
            if self.kernel_append is not None:
                kernel_strings += ["-append", self.kernel_append]

        sockets_strings = []
        for serial_object in self.__serials:
            sockets_strings += ["-serial",
                                "unix:{},server,nowait".format(serial_object["socket_file"])]

        self.__qmp_sock_file = os.path.join(self.__tmp_dir, "monitor.sock")
        sockets_strings += ["-monitor",
                            "unix:{},server".format(self.__qmp_sock_file)]

        boot_order_strings = []
        if self.__boot_order is not None:
            boot_order_strings = ["-boot", self.__boot_order]
        launch_args = ['qemu-system-x86_64',
                       "-enable-kvm",
                       "-nographic",
                       *cpu_strings,
                       *mem_strings,
                       *drive_strings,
                       *net_strings,
                       *pid_strings,
                       *sockets_strings,
                       *boot_order_strings,
                       *kernel_strings,
                       *self.qemu_command_line]
        launch_string = " ".join(launch_args)

        self.__process = Process(target=os.system, args=(launch_string,))
        self.__process.start()

        # XXX Qemu 2.5 (on ubuntu 16.04) doesn't support savevm/loadvm over qmp
        # @backoff.on_exception(backoff.constant,
        #                       Exception,
        #                       interval=1,
        #                       max_time=5)
        # def get_qmp_socket(qmp_sock_file):
        #     s = qmp.QEMUMonitorProtocol(qmp_sock_file)
        #     s.connect()
        #     return s
        # self.__qmp_socket = get_qmp_socket(self.__qmp_sock_file)

        self.__qmp_socket = get_serial_socket(self.__qmp_sock_file)
        self.__qmp_expect = fdpexpect.fdspawn(
            self.__qmp_socket, args=None, timeout=600, maxread=10240, encoding='utf-8')
        _path = os.path.join(self.__tmp_dir, "monitor.sock.log")
        self.__qmp_expect.logfile = open(_path, mode='w')
        self.__qmp_expect.expect_exact("(qemu)")

        for serial_object in self.__serials:
            serial_object["socket"] = get_serial_socket(
                serial_object["socket_file"])
            serial_object["expect"] = fdpexpect.fdspawn(
                serial_object["socket"], args=None, timeout=600, maxread=10240, encoding='utf-8', logfile=serial_object["logfd"], searchwindowsize=10240)

        self.pid = get_pid_of_qemu(self.pidfile)

        return self

    def __enter__(self):
        """
        When used in a with block, assembles a command line to run, and runs it
        in a multiprocessing call, providing control and serial sockets.
        """
        return self.start()

    def has_snapshot(self, snapshot_name):
        """
        Returns true if all Drives in this Machine have a snapshot with the name
        provided
        """
        return snapshot_name in self.snapshots

    @backoff.on_predicate(backoff.constant,
                          interval=1,
                          max_time=5)
    def kill_qemu(self):
        if os.path.exists("/proc/{}".format(self.pid)):
            os.kill(self.pid, signal.SIGINT)
            return False
        return True

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()

    def __del__(self):
        self.cleanup()

    def take_snapshot(self, name):
        """
        Wraps a "stop, savevm NAME, cont" set of commands to the qemu monitor
        """
        print("Taking snapshot:", name)
        self.__qmp_expect.expect_exact(["(qemu)", pexpect.TIMEOUT], timeout=1)
        self.__qmp_expect.sendline("stop")
        self.__qmp_expect.expect_exact("(qemu)")
        time.sleep(1)

        self.__qmp_expect.sendline("savevm {}\n".format(name))
        self.__qmp_expect.expect_exact("(qemu)")
        time.sleep(1)

        self.__qmp_expect.sendline("cont")
        self.__qmp_expect.expect_exact("(qemu)")

        time.sleep(1)

    class SnapshotNotFound(Exception):
        pass

    def delete_snapshot(self, name):
        """
        Deletes a vm snapshot with the name provided. Has no effect if there is
        no vm snapshot with that name
        """
        print("Deleting snapshot:", name)

        self.__qmp_expect.sendline("delvm {}".format(name))
        self.__qmp_expect.expect_exact("(qemu)")

        return

    def goto_snapshot(self, name):
        """
        Wraps a "stop, loadvm NAME, cont" set of commands to the qemu monitor.
        Reacquires serial sockets as there are some issues with older versions
        of qemu to do with sockets being closed on a loadvm call
        """
        # XXX Qemu 2.5 (on ubuntu 16.04) doesn't support savevm/loadvm over qmp
        # self.__qmp_socket.cmd("loadvm", args={"name": name})
        print("Going to snapshot:", name)
        self.__qmp_expect.sendline("stop")
        self.__qmp_expect.expect_exact("(qemu)")

        self.__qmp_expect.sendline("loadvm {}".format(name))
        self.__qmp_expect.expect_exact("(qemu)")
        rsp = self.__qmp_expect.before.splitlines()[1:]
        if any(["does not have the requested snapshot" in r for r in rsp]):
            raise Machine.SnapshotNotFound

        self.__qmp_expect.sendline("cont")
        self.__qmp_expect.expect_exact("(qemu)")

        return

    @property
    def snapshots(self):
        """
        Return a list of snapshots that exists in the top layer qcow2 for this
        drive. NOTE: This doesn't return snapshots that exist in lower layers
        """
        self.__qmp_expect.sendline("info snapshots")
        self.__qmp_expect.expect_exact("(qemu)")
        rsp = self.__qmp_expect.before.splitlines()[1:]

        rv = []

        if "There is no snapshot available." in rsp:
            return rv

        # Remove the "List of snapshots present on all disks:" line
        rsp = rsp[1:]

        for line in rsp:
            if line.startswith("ID"):
                continue
            rv += [line.split()[1]]
        return rv


@backoff.on_exception(backoff.constant,
                      Exception,
                      interval=1,
                      max_time=50)
def get_serial_socket(sock_file):
    """
    Returns an open socket to communicate to a Machine's configured serial
    port
    """
    s = socket.socket(family=socket.AF_UNIX)
    print("Connecting to", sock_file)
    s.connect(sock_file)
    return s
