#!/usr/bin/python

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
# Copyright: 2018 IBM
# Author: Bismruti Bidhibrata Pattjoshi <bbidhibr@in.ibm.com>
# Authors: Abdul haleem <abdhalee@linux.vnet.ibm.com>

'''
Tests for Sriov logical device
'''

import netifaces
try:
    import pxssh
except ImportError:
    from pexpect import pxssh
from avocado import Test
from avocado.utils import process
from avocado.utils.software_manager import SoftwareManager
from avocado.utils.network.interfaces import NetworkInterface
from avocado.utils.network.hosts import LocalHost


class CommandFailed(Exception):

    '''
    Defines the exception called when a
    command fails
    '''

    def __init__(self, command, output, exitcode):
        Exception.__init__(self, command, output, exitcode)
        self.command = command
        self.output = output
        self.exitcode = exitcode

    def __str__(self):
        return "Command '%s' exited with %d.\nOutput:\n%s" \
               % (self.command, self.exitcode, self.output)


class NetworkSriovDevice(Test):
    '''
    adding and deleting logical sriov device through
    HMC.
    '''
    def setUp(self):
        '''
        set up required packages and gather necessary test inputs
        '''
        smm = SoftwareManager()
        packages = ['src', 'rsct.basic', 'rsct.core.utils',
                    'rsct.core', 'DynamicRM', 'powerpc-utils']
        for pkg in packages:
            if not smm.check_installed(pkg) and not smm.install(pkg):
                self.cancel('%s is needed for the test to be run' % pkg)
        self.hmc_ip = self.get_mcp_component("HMCIPAddr")
        if not self.hmc_ip:
            self.cancel("HMC IP not got")
        self.hmc_pwd = self.params.get("hmc_pwd", '*', default=None)
        self.hmc_username = self.params.get("hmc_username", '*', default=None)
        self.lpar = self.get_partition_name("Partition Name")
        if not self.lpar:
            self.cancel("LPAR Name not got from lparstat command")
        self.login(self.hmc_ip, self.hmc_username, self.hmc_pwd)
        cmd = 'lssyscfg -r sys  -F name'
        output = self.run_command(cmd)
        self.server = ''
        for line in output.splitlines():
            if line in self.lpar:
                self.server = line
                break
        if not self.server:
            self.cancel("Managed System not got")
        self.sriov_adapter = self.params.get('sriov_adapter',
                                             '*', default=None).split(' ')
        self.sriov_port = self.params.get('sriov_port', '*',
                                          default=None).split(' ')
        self.ipaddr = self.params.get('ipaddr', '*', default="").split(' ')
        self.netmask = self.params.get('netmasks', '*', default="").split(' ')
        self.peer_ip = self.params.get('peer_ip', '*', default="").split(' ')
        self.mac_id = self.params.get('mac_id',
                                      default="02:03:03:03:03:01").split(' ')
        self.mac_id = [mac.replace(':', '') for mac in self.mac_id]
        self.local = LocalHost()

    @staticmethod
    def get_mcp_component(component):
        '''
        probes IBM.MCP class for mentioned component and returns it.
        '''
        for line in process.system_output('lsrsrc IBM.MCP %s' % component,
                                          ignore_status=True, shell=True,
                                          sudo=True).decode("utf-8") \
                                                    .splitlines():
            if component in line:
                return line.split()[-1].strip('{}\"')
        return ''

    @staticmethod
    def get_partition_name(component):
        '''
        get partition name from lparstat -i
        '''

        for line in process.system_output('lparstat -i', ignore_status=True,
                                          shell=True,
                                          sudo=True).decode("utf-8") \
                                                    .splitlines():
            if component in line:
                return line.split(':')[-1].strip()
        return ''

    def login(self, ipaddr, username, password):
        '''
        SSH Login method for remote server
        '''
        pxh = pxssh.pxssh(encoding='utf-8')
        # Work-around for old pxssh not having options= parameter
        pxh.SSH_OPTS = "%s  -o 'StrictHostKeyChecking=no'" % pxh.SSH_OPTS
        pxh.SSH_OPTS = "%s  -o 'UserKnownHostsFile /dev/null' " % pxh.SSH_OPTS
        pxh.force_password = True

        pxh.login(ipaddr, username, password)
        pxh.sendline()
        pxh.prompt(timeout=60)
        pxh.sendline('exec bash --norc --noprofile')
        pxh.prompt(timeout=60)
        # Ubuntu likes to be "helpful" and alias grep to
        # include color, which isn't helpful at all. So let's
        # go back to absolutely no messing around with the shell
        pxh.set_unique_prompt()
        pxh.prompt(timeout=60)
        self.pxssh = pxh

    def run_command(self, command, timeout=300):
        '''
        SSH Run command method for running commands on remote server
        '''
        self.log.info("Running the command on peer lpar: %s", command)
        if not hasattr(self, 'pxssh'):
            self.fail("SSH Console setup is not yet done")
        con = self.pxssh
        con.sendline(command)
        con.expect("\n")  # from us
        con.expect(con.PROMPT, timeout=timeout)
        output = "".join(con.before)
        con.sendline("echo $?")
        con.prompt(timeout)
        exitcode = int(''.join(con.before.splitlines()[1:]))
        if exitcode != 0:
            raise CommandFailed(command, output, exitcode)
        return output

    def test_add_logical_device(self):
        '''
        test to create logical sriov device
        '''
        for slot, port, mac, ipaddr, netmask, peer_ip in zip(self.sriov_adapter,
                                                             self.sriov_port,
                                                             self.mac_id, self.ipaddr,
                                                             self.netmask, self.peer_ip):
            self.device_add_remove(slot, port, mac, '', 'add')
            if not self.list_device(mac):
                self.fail("failed to list logical device after add operation")
            device = self.find_device(mac)
            networkinterface = NetworkInterface(device, self.local)
            networkinterface.add_ipaddr(ipaddr, netmask)
            networkinterface.bring_up()
            if networkinterface.ping_check(peer_ip, count=5) is not None:
                self.fail("ping check failed")

    def test_remove_logical_device(self):
        """
        test to remove logical device
        """
        for mac, slot in zip(self.mac_id, self.sriov_adapter):
            logical_port_id = self.get_logical_port_id(mac)
            self.device_add_remove(slot, '', '', logical_port_id, 'remove')
            if self.list_device(mac):
                self.fail("still list logical device after remove operation")

    def device_add_remove(self, slot, port, mac, logical_id, operation):
        """
        add and remove operation of logical devices
        """
        cmd = "lshwres -m %s -r sriov --rsubtype adapter -F phys_loc:adapter_id" \
              % (self.server)
        output = self.run_command(cmd)
        for line in output.splitlines():
            if slot in line:
                adapter_id = line.split(':')[-1]

        if operation == 'add':
            cmd = 'chhwres -r sriov -m %s --rsubtype logport \
                  -o a -p %s -a \"adapter_id=%s,phys_port_id=%s, \
                  logical_port_type=eth,mac_addr=%s\" ' \
                  % (self.server, self.lpar, adapter_id,
                     port, mac)
        else:
            cmd = 'chhwres -r sriov -m %s --rsubtype logport \
                  -o r -p %s -a \"adapter_id=%s,logical_port_id=%s\" ' \
                  % (self.server, self.lpar, adapter_id, logical_id)
        try:
            self.run_command(cmd)
        except CommandFailed as cf:
            self.fail("sriov logical device %s operation \
                       failed as %s" % (operation, cf))

    def get_logical_port_id(self, mac):
        """
        findout logical device port id
        """
        cmd = "lshwres -r sriov --rsubtype logport -m  %s \
               --level eth | grep %s | grep %s" \
               % (self.server, self.lpar, mac)
        output = self.run_command(cmd)
        logical_port_id = output.split(',')[6].split('=')[-1]
        return logical_port_id

    @staticmethod
    def find_device(mac_addrs):
        """
        Finds out the latest added sriov logical device
        """
        mac = ':'.join(mac_addrs[i:i+2] for i in range(0, 12, 2))
        devices = netifaces.interfaces()
        for device in devices:
            if mac in netifaces.ifaddresses(device)[17][0]['addr']:
                return device
        return ''

    def list_device(self, mac):
        """
        list the sriov logical device
        """
        cmd = 'lshwres -r sriov --rsubtype logport -m  ltcfleet2 \
              --level eth --filter \"lpar_names=%s\" ' % self.lpar
        output = self.run_command(cmd)
        if mac in output:
            return True
        return False

    def tearDown(self):
        if self.pxssh.isalive():
            self.pxssh.terminate()
