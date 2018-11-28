"""
Handles the creation of a '-drive' parameter to qemu, including snapshots and
creating temporary disks as layers.
"""

import tempfile
import os
import subprocess


class Drive():
    """
    A Drive device can be turned into a string to pass as an argument to qemu,
    can produce a list of snapshots it contains, and can easily be used to roll
    back disk changes for tests.
    """

    def __init__(self, file, interface="ide", media="disk", index=None):
        self.backing_file = file
        self.interface = interface
        self.media = media
        self.__layers = [file]
        self.index = index

    def create_disk_layer(self, machine_instance_path):
        """
        Makes a new qcow2 that backs the main qcow2, and sets the new qcow2 path
        to the top of the stack
        """
        current_layer = self.__layers[-1]
        (fd, name) = tempfile.mkstemp(suffix=".qcow2",
                                      prefix="drive_", dir=machine_instance_path)
        os.close(fd)
        cmd_string = "qemu-img create -b {} -f qcow2 {}".format(
            current_layer, name)
        os.system(cmd_string)
        self.__layers.append(name)
        return name

    def delete_disk_layer(self):
        """
        Removes the top layer qcow2 path from the path stack
        """
        if len(self.__layers) > 1:
            return self.__layers.pop()
        return None

    def to_drive_string(self):
        """
        Provides a string that can be passed as an argument to a -drive argument
        in qemu, pointing to the top layer in the stack
        """
        strings = []
        if self.interface is not None:
            strings += ["if={}".format(self.interface)]
        strings += ["file={}".format(self.__layers[-1])]
        if self.index is not None:
            strings += ["index={}".format(self.index)]

        return ",".join(strings)

    @property
    def snapshots(self):
        """
        Return a list of snapshots that exists in the top layer qcow2 for this
        drive. NOTE: This doesn't return snapshots that exist in lower layers
        """
        lines = subprocess.check_output(
            ["bash", "-c", "qemu-img snapshot -l {} | tail -n +3 | awk '{{print $2}}'".format(self.__layers[-1])]).decode().split("\n")
        return lines
