#!/usr/bin/env python2.7
# -------------------------------------------------------------------------------
# Name:         ServersConnectivityTester.py
# Purpose:      Initial checks to figure out which servers supplied by the
#               user are actually reachable.
#
# Author:       alban
#
# Copyright:    2013 SSLyze developers
#
#   SSLyze is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 2 of the License, or
#   (at your option) any later version.
#
#   SSLyze is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with SSLyze.  If not, see <http://www.gnu.org/licenses/>.
# -------------------------------------------------------------------------------

import socket
from xml.etree.ElementTree import Element
from ThreadPool import ThreadPool
from nassl import SSLV23, SSLV3, TLSV1, TLSV1_2
from SSLyzeSSLConnection import create_sslyze_connection, StartTLSError, ProxyError


class InvalidTargetError(Exception):
    RESULT_FORMAT = '\n   {0:<35} => WARNING: {1}; discarding corresponding tasks.'

    def __init__(self, target_str, error_msg):
        self._target_str = target_str
        self._error_msg = error_msg

    def get_error_txt(self):
        return self.RESULT_FORMAT.format(self._target_str, self._error_msg)

    def get_error_xml(self):
        errorXml = Element('invalidTarget', error=self._error_msg)
        errorXml.text = self._target_str
        return errorXml


class TargetStringParser(object):
    """Utility class to parse a 'host:port{ip}' string taken from the command line into a valid (host,ip, port) tuple.
    Supports IPV6 addresses.
    """

    ERR_BAD_PORT = 'Not a valid host:port'
    ERR_NO_IPV6 = 'IPv6 is not supported on this platform'

    @classmethod
    def parse_target_str(cls, target_str, default_port):
        # extract ip from target
        if '{' in target_str and '}' in target_str:
            raw_target = target_str.split('{')
            raw_ip = raw_target[1]

            ip = raw_ip.replace('}', '')

            # clean the target
            target_str = raw_target[0]
        else:
            ip = None

        # Look for ipv6 hint in target
        if '[' in target_str:
            (host, port) = cls._parse_ipv6_target_str(target_str, default_port)
        else:
            # Look for ipv6 hint in the ip
            if ip is not None and '[' in ip:
                (ip, port) = cls._parse_ipv6_target_str(ip, default_port)

            # Fallback to ipv4
            (host, port) = cls._parse_ipv4_target_str(target_str, default_port)

        return host, ip, port

    @classmethod
    def _parse_ipv4_target_str(cls, target_str, default_port):

        if ':' in target_str:
            host = (target_str.split(':'))[0]  # hostname or ipv4 address
            try:
                port = int((target_str.split(':'))[1])
            except:  # Port is not an int
                raise InvalidTargetError(target_str, cls.ERR_BAD_PORT)
        else:
            host = target_str
            port = default_port

        return host, port

    @classmethod
    def _parse_ipv6_target_str(cls, target_str, default_port):

        if not socket.has_ipv6:
            raise InvalidTargetError(target_str, cls.ERR_NO_IPV6)

        port = default_port
        target_split = (target_str.split(']'))
        ipv6_addr = target_split[0].split('[')[1]
        if ':' in target_split[1]:  # port was specified
            try:
                port = int(target_split[1].rsplit(':')[1])
            except:  # Port is not an int
                raise InvalidTargetError(target_str, cls.ERR_BAD_PORT)
        return ipv6_addr, port


class ServersConnectivityTester(object):
    """Utility class to connect to a list of servers and return a list of online and offline servers.
    """

    HOST_FORMAT = '{0[0]}:{0[2]}'
    IP_FORMAT = '{0[1]}:{0[2]}'
    TARGET_OK_FORMAT = '\n   {0:<35} => {1}'

    MAX_THREADS = 50

    DEFAULT_PORTS = {'smtp': 25,
                     'xmpp': 5222,
                     'xmpp_server': 5269,
                     'ftp': 21,
                     'pop3': 110,
                     'ldap': 389,
                     'imap': 143,
                     'rdp': 3389,
                     'default': 443}

    ERR_TIMEOUT = 'Could not connect (timeout)'
    ERR_NAME_NOT_RESOLVED = 'Could not resolve hostname'
    ERR_REJECTED = 'Connection rejected'

    @classmethod
    def test_server_list(cls, target_list, shared_settings):
        """
        Tests connectivity with each server of the target_list and returns
        the list of online servers.
        """

        # Use a thread pool to connect to each server
        thread_pool = ThreadPool()
        for target_str in target_list:
            thread_pool.add_job((cls._test_server, (target_str, shared_settings)))

        nb_threads = min(len(target_list), cls.MAX_THREADS)
        thread_pool.start(nb_threads)

        # Return valid targets
        for (job, target) in thread_pool.get_result():
            yield target

        # Use None as a sentinel
        yield None

        # Return invalid targets
        for (job, exception) in thread_pool.get_error():
            yield exception

        thread_pool.join()
        return

    @classmethod
    def get_printable_result(cls, targets_ok, targets_err):
        """Returns a text meant to be displayed to the user and presenting the results of the connectivity testing.
        """
        result_str = ''
        for target in targets_ok:
            result_str += cls.TARGET_OK_FORMAT.format(cls.HOST_FORMAT.format(target), cls.IP_FORMAT.format(target))

        for exception in targets_err:
            result_str += exception.get_error_txt()

        return result_str

    @classmethod
    def get_xml_result(cls, targets_err):
        """Returns XML containing the list of every target that returned an error during the connectivity testing.
        """
        result_xml = Element('invalidTargets')
        for exception in targets_err:
            result_xml.append(exception.get_error_xml())

        return result_xml

    @classmethod
    def _test_server(cls, target_from_cmd_line, shared_settings):
        """Test connectivity to one single server.
        """

        # Parse the target string
        default_port = cls.DEFAULT_PORTS.get(shared_settings['starttls'], default=cls.DEFAULT_PORTS['default'])
        (host, ip, port) = TargetStringParser.parse_target_str(target_from_cmd_line, default_port)

        # Check if the ip was specified
        if not ip:
            ip = host

        # First try to connect and do StartTLS if needed
        ssl_connection = create_sslyze_connection((host, ip, port, SSLV23), shared_settings)
        try:
            ssl_connection.do_pre_handshake()
            ip_address = ssl_connection._sock.getpeername()[0]

        # Socket errors
        except socket.timeout:  # Host is down
            raise InvalidTargetError(target_from_cmd_line, cls.ERR_TIMEOUT)
        except socket.gaierror:
            raise InvalidTargetError(target_from_cmd_line, cls.ERR_NAME_NOT_RESOLVED)
        except socket.error:  # Connection Refused
            raise InvalidTargetError(target_from_cmd_line, cls.ERR_REJECTED)

        # StartTLS errors
        except StartTLSError as e:
            raise InvalidTargetError(target_from_cmd_line, e[0])

        # Proxy errors
        except ProxyError as e:
            raise InvalidTargetError(target_from_cmd_line, e[0])

        # Other errors
        except Exception as e:
            raise InvalidTargetError(target_from_cmd_line, '{0}: {1}'.format(str(type(e).__name__), e[0]))


        finally:
            ssl_connection.close()

        # Then try to do SSL handshakes just to figure out the SSL version
        # supported by the server; the plugins need to know this in advance.
        # If the handshakes fail, we keep going anyway; maybe the server
        # only supports exotic cipher suites
        ssl_version_supported = SSLV23
        # No connection retry when testing connectivity
        local_shared_settings = shared_settings.copy()
        local_shared_settings['nb_retries'] = 1
        for ssl_version in [TLSV1, SSLV23, SSLV3, TLSV1_2]:
            ssl_connection = create_sslyze_connection((host, ip_address, port, ssl_version), local_shared_settings)
            try:
                ssl_connection.connect()
            except:
                pass
            else:
                ssl_version_supported = ssl_version
                break
            finally:
                ssl_connection.close()

        return host, ip_address, port, ssl_version_supported
