"""
Wraps a bunch of calls to ip and brctl to help abstract connecting machines to
each other, and to host bridges
"""

import os
import re
from scapy.all import sniff

DHCP_SNIFF_TIMEOUT = 10

bridge_counter = 0
veth_counter = 0

# XXX FIXME: Rewrite all the inheritance here - it's a bit garbage


class Adapter():
    """
    Wraps a Bridge/Adapter link so that when QEmu creates a tapX device, we can
    have this attached to a bridge, and then trivially add veth pairs to
    connect and disconnect bridges from each other.
    """

    def __init__(self, mac_addr):
        self.mac_addr = mac_addr
        self.uid = mac_addr.replace(":", "")[-6:]
        self.__br_name = "br-{}".format(self.uid)
        self.__ip_address = None

    @property
    def ip_address(self):
        return self.__ip_address

    @ip_address.setter
    def ip_address(self, v):
        self.__ip_address = v
        return self.__ip_address

    @property
    def name(self):
        return self.__br_name

    def create_bridge(self):
        os.system("brctl addbr {}".format(self.__br_name))
        os.system("ip link set {} up".format(self.__br_name))
        return self.__br_name

    def delete_bridge(self):
        os.system("ip link set {} down".format(self.__br_name))
        os.system("brctl delbr {}".format(self.__br_name))


class Bridge():
    """
    Creates a bridge device, and allows it to connect to other bridges/adapters
    by creating veth pairs between them
    """

    def __init__(self):
        global bridge_counter
        bridge_num = "{0:03d}".format(bridge_counter)
        bridge_counter += 1

        self.uid = bridge_num
        self.__br_name = "br-w{}".format(self.uid)
        self.veth_pairs = []

    @property
    def name(self):
        return self.__br_name

    @property
    def ip_address(self):
        return self.__ip_address

    @ip_address.setter
    def ip_address(self, v):
        self.__ip_address = v
        return self.__ip_address

    def create_bridge(self):
        os.system("brctl addbr {}".format(self.name))
        os.system("ip link set {} up".format(self.name))
        return self.name

    def delete_bridge(self):
        os.system("ip link set {} down".format(self.name))
        os.system("brctl delbr {}".format(self.name))

    def delay(self, time=0, jitter=0):
        pass

    def __enter__(self):
        self.create_bridge()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()

    def add_adapter(self, adapter, wait_for_dhcp=False, dhcp_timeout=DHCP_SNIFF_TIMEOUT):
        """
        Adds an Adapter to this bridge by creating a veth pair between the
        bridge of the target Adapter and this one
        """
        # XXX FIXME Make this count better than just incrementing from zero
        global veth_counter
        name1 = "wveth{0:03d}".format(veth_counter)
        veth_counter += 1
        name2 = "wveth{0:03d}".format(veth_counter)
        veth_counter += 1
        for s in ["ip link add {} type veth peer name {}".format(name1, name2),
                  "ip link set {} master {}".format(name1, self.name),
                  "ip link set {} master {}".format(name2, adapter.name),
                  "ip link set {} up".format(name1),
                  "ip link set {} up".format(name2)
                  ]:
            print(s, flush=True)
            os.system(s)

        self.veth_pairs += [(name1, name2)]

        if wait_for_dhcp:
            packets = sniff(count=1,
                            store=True,
                            offline=None,
                            prn=None,
                            lfilter=None,
                            L2socket=None,
                            timeout=dhcp_timeout,
                            opened_socket=None,
                            filter="udp and port 67 and ether dst {}".format(
                                adapter.mac_addr),
                            stop_filter=lambda packet: packet is not None and packet.getlayer(
                                4) is not None and ("message-type", 5) in packet.getlayer(4).options,
                            iface=self.name)

            if len(packets) > 0:
                print("Got DHCP packet on {}".format(self.name))
                if packets[0].getlayer(3).yiaddr is not None:
                    new_ip_addr = packets[0].getlayer(3).yiaddr
                    print("Assigning {} to adapter with mac {}".format(
                        new_ip_addr, adapter.mac_addr))
                    adapter.ip_address = new_ip_addr

        return

    def release(self):
        """
        Called by the __exit__ function to clean up all links created, but
        should be called manually if not used in a 'with' block
        """
        for p1, p2 in self.veth_pairs:
            for s in ["ip link set {} down".format(p1),
                      "ip link set {} down".format(p2),
                      "ip link delete {}".format(p1)]:
                print(s)
                os.system(s)


class Static(Bridge):
    def __init__(self):
        global bridge_counter
        self.mac_addr = None
        self.uid = "s-{0:03d}".format(bridge_counter)
        bridge_counter += 1
        self.__br_name = "br-{}".format(self.uid)
        self.veth_pairs = []
        super().create_bridge()

    @property
    def name(self):
        return self.__br_name

    def release(self):
        super().release()
        super().delete_bridge()

    def __enter__(self):
        os.system("ip link add {} type bridge".format(self.name))
        super().__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        super().__exit__(exc_type, exc_value, traceback)
        os.system("ip link delete {}".format(self.name))


class Host(Bridge):
    """
    Use a pre-existing bridge on the host. The bridge is not created nor
    destroyed, but can be used by other convenience functions
    """

    def __init__(self, host_bridge_name):
        self.__br_name = host_bridge_name
        self.veth_pairs = []

    @property
    def name(self):
        return self.__br_name

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()


def adapters_from_mac_list(mac_list):
    rv = []
    colon_mac = re.compile("^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
    compact_mac = re.compile("^[0-9a-fA-F]{12}$")
    for mac in mac_list:
        if colon_mac.match(mac):
            rv.append(Adapter(mac.upper()))
        elif compact_mac.match(mac):
            splitmac = [mac[(2*b):(2*b)+2] for b in range(0, 6)]
            rv.append(Adapter(":".join(splitmac)))
    return rv
