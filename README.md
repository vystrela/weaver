# weaver

Use QEmu virtual machines as objects in python, connect network adapters to different virtual networks, and create/revert snapshots. Lighter-weight than OpenStack, relies only on iproute2 and qemu binaries outside of python.

Work in progress - you'd have to be desperate and/or actively developing it to use this in production.


## Example:
```
import weaver
import pytest
import requests

@pytest.fixture
def ubuntu():
    main_drive = weaver.Drive(file="/path/to/ubuntu-18-04.qcow2", interface="ide")
    net1 = weaver.Network.Adapter("52:54:00:00:01:01")

    with weaver.Machine(cpus=2,
                        mem=4096,
                        drives=[main_drive],
                        net=[net1]) as m:
        yield m
    return

def test_ubuntu_machine_starts(ubuntu):
    # Wait for a login prompt over serial
    ubuntu.serial_expect.expect("ubuntu login:", timeout=30)

    # Connect it to the host bridge `bridge0` which has your dhcpd running, and
    # wait up to 10 seconds for it to get an ipv4 address
    with weaver.Network.Host("bridge0") as dhcp_network:
        dhcp_network.add_adapter(ubuntu.net[0],
                                 wait_for_dhcp=True,
                                 dhcp_timeout=10)

        # Issue a get request to the running web server
        response = requests.get("https://{}/".format(ubuntu.net[0].ip_address))

        assert (response.status_code == 200)

        # Web server is running, take a snapshot
        ubuntu.take_snapshot("webserver_running")


```

___


Thanks to [Opengear](https://github.com/opengear) for providing me time and support in the development of this module.