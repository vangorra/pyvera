"""Module to listen for vera events."""
import collections
import logging
import time
import threading
import requests

SUBSCRIPTION_RETRY = 60
# Time to wait for event in seconds

# Vera stae codes see http://wiki.micasaverde.com/index.php/Luup_Requests
STATE_NO_JOB = -1
STATE_JOB_WAITING_TO_START = 0
STATE_JOB_IN_PROGRESS = 1
STATE_JOB_ERROR = 2
STATE_JOB_ABORTED = 3
STATE_JOB_DONE = 4
STATE_JOB_WAITING_FOR_CALLBACK = 5
STATE_JOB_REQUEUE = 6
STATE_JOB_PENDING_DATA = 7


LOG = logging.getLogger(__name__)


class SubscriptionRegistry(object):
    """Class for subscribing to wemo events."""

    def __init__(self):
        self._devices = {}
        self._callbacks = collections.defaultdict(list)
        self._exiting = False
        self._poll_thread = None

    def register(self, device, callback):
        if not device:
            LOG.error("Received an invalid device: %r", device)
            return

        LOG.info("Subscribing to events for %s", device.name)
        self._devices[device.vera_device_id] = device
        self._callbacks[device].append((callback))

    def _event(self, device_data_list):
        for device_data in device_data_list:
            device_id = device_data['id']
            state = int(device_data.get('state', STATE_NO_JOB))
            device = self._devices.get(int(device_id))
            if device is None:
                continue
            if (
                    state == STATE_JOB_WAITING_TO_START or
                    state == STATE_JOB_IN_PROGRESS or
                    state == STATE_JOB_WAITING_FOR_CALLBACK or
                    state == STATE_JOB_REQUEUE or
                    state == STATE_JOB_PENDING_DATA):
                LOG.warning("Pending: device %s, state %s, %s",
                            device.name,
                            state,
                            device_data.get('comment', ''))
                continue
            if not (state == STATE_JOB_DONE or
                    state == STATE_NO_JOB):
                LOG.error("Device %s, state %s, %s", device.name, state,
                          device_data.get('comment', ''))
                continue
            device.update(device_data)
            for callback in self._callbacks.get(device, ()):
                callback(device)

    def join(self):
        self._poll_thread.join()

    def start(self):
        self._poll_thread = threading.Thread(target=self._run_poll_server,
                                             name='Vera Poll Thread')
        self._poll_thread.deamon = True
        self._poll_thread.start()

    def stop(self):
        self._exiting = True

    def _run_poll_server(self):
        from pyvera import get_controller
        controller = get_controller()
        timestamp = None
        # Wait for code to initialize to avoid callbacks before ready
        # Initial state callbacks are instant!
        time.sleep(10)
        while not self._exiting:
            try:
                device_data, timestamp = (
                    controller.get_changed_devices(timestamp))
                if self._exiting:
                    continue
                if not device_data:
                    LOG.info("No changes in poll interval")
                    continue
                self._event(device_data)
                time.sleep(1)
            except requests.RequestException:
                LOG.info("Could not contact Vera - will retry in %ss",
                         SUBSCRIPTION_RETRY)
                time.sleep(SUBSCRIPTION_RETRY)

        LOG.info("Shutdown Vera Poll Thread")
