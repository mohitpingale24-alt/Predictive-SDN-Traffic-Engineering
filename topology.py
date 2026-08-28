#!/usr/bin/env python3
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info
import time


def build_topology():
    net = Mininet(controller=RemoteController, switch=OVSSwitch, link=TCLink, autoSetMacs=True)

    info('*** Adding controller\n')
    c0 = net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6633)

    info('*** Adding hosts\n')
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')

    info('*** Adding switches\n')
    s1 = net.addSwitch('s1', protocols='OpenFlow13', stp=True, failMode='standalone')
    s2 = net.addSwitch('s2', protocols='OpenFlow13', stp=True, failMode='standalone')
    s3 = net.addSwitch('s3', protocols='OpenFlow13', stp=True, failMode='standalone')
    s4 = net.addSwitch('s4', protocols='OpenFlow13', stp=True, failMode='standalone')

    info('*** Creating links\n')
    net.addLink(h1, s1)
    net.addLink(h2, s4)
    net.addLink(s1, s2, bw=10)
    net.addLink(s2, s4, bw=10)
    net.addLink(s1, s3, bw=10)
    net.addLink(s3, s4, bw=10)

    info('*** Starting network\n')
    net.build()
    c0.start()
    for sw in [s1, s2, s3, s4]:
        sw.start([c0])

    info('*** Waiting for STP to converge (about 45 seconds)...\n')
    time.sleep(45)

    info('*** Network is up. Dropping into Mininet CLI.\n')
    info('*** Try: h1 ping h2\n')
    CLI(net)

    info('*** Stopping network\n')
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    build_topology()
