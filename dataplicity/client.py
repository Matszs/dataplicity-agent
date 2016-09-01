from __future__ import unicode_literals
from __future__ import print_function

import logging
import platform
from threading import Event, Lock
import random
import time

from _version import __version__
from . import constants
from . import device_meta
from . import jsonrpc
from . import settings
from . import tools
from .disk_tools import disk_usage
from .m2mmanager import M2MManager
from .portforward import PortForwardManager

log = logging.getLogger('agent')


class Client(object):
    """Dataplicity client."""

    def __init__(self, conf_path, rpc_url=None):
        self.conf_path = conf_path
        self.rpc_url = rpc_url
        self._sync_lock = Lock()
        self._sent_meta = False
        self.exit_event = Event()
        self._init()

    def _init(self):
        try:
            log.info('dataplicity %s', __version__)
            log.info('uname=%s', ' '.join(platform.uname()))

            conf = self.conf = settings.read(self.conf_path)
            if self.rpc_url is None:
                self.rpc_url = conf.get(
                    'server',
                    'url',
                    constants.SERVER_URL
                )

            self.remote = jsonrpc.JSONRPC(self.rpc_url)
            self.serial = tools.resolve_value(conf.get('device', 'serial'))
            self.auth_token = tools.resolve_value(conf.get('device', 'auth'))
            self.poll_rate_seconds = conf.get_float("daemon", "poll", 60.0)
            self.disk_poll_rate_seconds = conf.get_integer("daemon", "disk_poll", 60 * 60)
            self.next_disk_poll_time = time.time()

            log.info('api=%s', self.rpc_url)
            log.info('serial=%s', self.serial)
            log.info('poll=%s', self.poll_rate_seconds)

            self.m2m = M2MManager.init_from_conf(self, conf)
            self.port_forward = PortForwardManager.init_from_conf(self, conf)

        except:
            log.exception('failed to initialize client')
            raise

    def run_forever(self):
        """Run the client "forever"."""
        try:
            self.poll()
            while not self.exit_event.wait(self.poll_rate_seconds):
                self.poll()
        except SystemExit:
            log.debug('exit requested')
            return
        except KeyboardInterrupt:
            log.debug('user exit')
            return
        finally:
            log.debug('closing')
            self.close()
            log.debug('goodbye')

    def disk_poll(self):
        now = time.time()
        if now >= self.next_disk_poll_time:
            self.next_disk_poll_time = now + self.disk_poll_rate_seconds
            disk_space = disk_usage('/')

            with self.remote.batch() as batch:
                batch.call_with_id(
                    'authenticate_result',
                    'device.check_auth',
                    device_class='tuxtunnel',
                    serial=self.serial,
                    auth_token=self.auth_token
                )
                batch.call_with_id(
                    'set_disk_space_result',
                    'device.set_disk_space',
                    disk_capacity=disk_space.total,
                    disk_used=disk_space.used
                )

    def poll(self):
        """Called at regulat intervals."""
        t = time.time()
        log.debug('poll t=%.02fs', t)
        self.disk_poll()
        self.sync()

    def close(self):
        """Perform shutdown."""
        pass

    @classmethod
    def make_sync_id(cls):
        """Make a random sync ID."""
        sync_id = ''.join(
            random.choice('abcdefghijklmnopqrstuvwxyz') for _ in xrange(12)
        )
        return sync_id

    def sync(self):
        try:
            with self._sync_lock:
                self._sync()
        except Exception as e:
            log.error("sync failed %s", e)

    def _sync(self):
        start = time.time()
        sync_id = self.make_sync_id()
        try:
            with self.remote.batch() as batch:
                batch.call_with_id(
                    'authenticate_result',
                    'device.check_auth',
                    device_class='tuxtunnel',
                    serial=self.serial,
                    auth_token=self.auth_token,
                    sync_id=sync_id
                )
                if not self._sync_meta(batch):
                    batch.abandon()

            if batch.sent:
                # get_result will throw exceptions with (hopefully) helpful
                # error messages if they fail
                batch.get_result('authenticate_result')
                self._check_meta(batch)

        finally:
            ellapsed = time.time() - start
            log.debug('sync complete %0.2fs', ellapsed)

    def _sync_meta(self, batch):
        """Sync meta information regarding host device."""
        if self._sent_meta:
            return False

        try:
            meta = device_meta.get_meta()
        except:
            log.exception('error getting meta')
        else:
            batch.call_with_id(
                'set_agent_version_result',
                'device.set_agent_version',
                agent_version=meta['agent_version']
            )
            batch.call_with_id(
                'set_machine_type_result',
                'device.set_machine_type',
                machine_type=meta['machine_type'] or 'other'
            )
            batch.call_with_id(
                'set_os_version_result',
                'device.set_os_version',
                os_version=meta['os_version']
            )
            batch.call_with_id(
                'set_uname_result',
                'device.set_uname',
                uname=meta['uname']
            )

    def _check_meta(self, batch):
        """Check previously sent meta information."""
        if self._sent_meta:
            return
        try:
            batch.check(
                'set_agent_version_result',
                'set_machine_type_result',
                'set_os_version_result',
                'set_uname_result'
            )
        except Exception as e:
            self.log.warning('failed to set device meta (%s)', e)
        else:
            # Success! Don't send again.
            self._sent_meta = True

    def set_m2m_identity(self, identity):
        """
        Tell the server of our m2m identity, return the identity if it was set,
        or None if it could not be set.
        """
        if self.auth_token is None:
            if not self.disable_sync:
                self.log.debug("skipping m2m identity notify because we don't have an auth token")
            return None

        try:
            log.debug('notiying server (%s) of m2m identity (%s)',
                      self.remote.url,
                      identity or '<None>')
            with self.remote.batch() as batch:
                batch.call_with_id('authenticate_result',
                                   'device.check_auth',
                                   device_class='tuxtunnel',
                                   serial=self.serial,
                                   auth_token=self.auth_token)
                batch.call_with_id('associate_result',
                                   'm2m.associate',
                                   identity=identity or '')
            # These methods may potentially throw JSONRPCErrors
            batch.get_result('authenticate_result')
            batch.get_result('associate_result')
        except jsonrpc.JSONRPCError as e:
            log.error('unable to associate m2m identity ("%s"=%s, "%s")',
                      e.method, e.code, e.message)
            return None
        except jsonrpc.ServerUnreachableError as e:
            log.debug('set m2m identity failed, %s', e)
        except:
            log.error('unable to set m2m identity')
            return None
        else:
            # If we made it here the server has acknowledged it received the identity
            # It will be sent again on sync anyway, as a precaution
            log.debug('server received m2m identity %s', identity)
            return identity
