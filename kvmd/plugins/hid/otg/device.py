# ========================================================================== #
#                                                                            #
#    KVMD - The main Pi-KVM daemon.                                          #
#                                                                            #
#    Copyright (C) 2018-2021  Maxim Devaev <mdevaev@gmail.com>               #
#                                                                            #
#    This program is free software: you can redistribute it and/or modify    #
#    it under the terms of the GNU General Public License as published by    #
#    the Free Software Foundation, either version 3 of the License, or       #
#    (at your option) any later version.                                     #
#                                                                            #
#    This program is distributed in the hope that it will be useful,         #
#    but WITHOUT ANY WARRANTY; without even the implied warranty of          #
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           #
#    GNU General Public License for more details.                            #
#                                                                            #
#    You should have received a copy of the GNU General Public License       #
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.  #
#                                                                            #
# ========================================================================== #


import os
import select
import multiprocessing
import queue
import errno
import time

from typing import Dict
from typing import Generator

from ....logging import get_logger

from .... import tools
from .... import aiomulti
from .... import aioproc

from .usb import UsbDeviceController
from .events import BaseEvent


# =====
class BaseDeviceProcess(multiprocessing.Process):  # pylint: disable=too-many-instance-attributes
    def __init__(  # pylint: disable=too-many-arguments
        self,
        name: str,
        read_size: int,
        initial_state: Dict,
        notifier: aiomulti.AioProcessNotifier,

        udc: UsbDeviceController,

        device_path: str,
        select_timeout: float,
        queue_timeout: float,
        write_retries: int,
        noop: bool,
    ) -> None:

        super().__init__(daemon=True)

        self.__name = name
        self.__read_size = read_size

        self.__udc = udc

        self.__device_path = device_path
        self.__select_timeout = select_timeout
        self.__queue_timeout = queue_timeout
        self.__write_retries = write_retries
        self.__noop = noop

        self.__fd = -1
        self.__events_queue: "multiprocessing.Queue[BaseEvent]" = multiprocessing.Queue()
        self.__state_flags = aiomulti.AioSharedFlags({"online": True, **initial_state}, notifier)
        self.__stop_event = multiprocessing.Event()

    def run(self) -> None:  # pylint: disable=too-many-branches
        logger = aioproc.settle(f"HID-{self.__name}", f"hid-{self.__name}")
        report = b""
        retries = 0
        while not self.__stop_event.is_set():
            try:
                while not self.__stop_event.is_set():
                    if self.__ensure_device():
                        self.__read_all_reports()

                    try:
                        event = self.__events_queue.get(timeout=self.__queue_timeout)
                    except queue.Empty:
                        if not self.__udc.can_operate():
                            self.__state_flags.update(online=False)
                    else:
                        # Посылка свежих репортов важнее старого
                        for report in self._process_event(event):
                            retries = self.__write_retries
                            if self.__ensure_device():
                                if self.__write_report(report):
                                    retries = 0
                        continue

                    # Повторение последнего репорта до победного или пока не кончатся попытки
                    if retries > 0 and self.__ensure_device():
                        if self.__write_report(report):
                            retries = 0
                        else:
                            retries -= 1

            except Exception:
                logger.exception("Unexpected HID-%s error", self.__name)
                time.sleep(1)

        self.__close_device()

    async def get_state(self) -> Dict:
        return (await self.__state_flags.get())

    # =====

    def _process_event(self, event: BaseEvent) -> Generator[bytes, None, None]:  # pylint: disable=unused-argument
        if self is not None:  # XXX: Vulture and pylint hack
            raise NotImplementedError()
        yield

    def _process_read_report(self, report: bytes) -> None:
        pass

    def _update_state(self, **kwargs: bool) -> None:
        assert "online" not in kwargs
        self.__state_flags.update(**kwargs)

    # =====

    def _stop(self) -> None:
        if self.is_alive():
            get_logger().info("Stopping HID-%s daemon ...", self.__name)
            self.__stop_event.set()
        if self.is_alive() or self.exitcode is not None:
            self.join()

    def _queue_event(self, event: BaseEvent) -> None:
        self.__events_queue.put_nowait(event)

    def _clear_queue(self) -> None:
        tools.clear_queue(self.__events_queue)

    def _cleanup_write(self, report: bytes) -> None:
        assert not self.is_alive()
        assert self.__fd < 0
        if self.__ensure_device():
            self.__write_report(report)
            self.__close_device()

    # =====

    def __write_report(self, report: bytes) -> bool:
        assert report

        if self.__noop:
            return True

        assert self.__fd >= 0
        logger = get_logger()

        try:
            written = os.write(self.__fd, report)
            if written == len(report):
                self.__state_flags.update(online=True)
                return True
            else:
                logger.error("HID-%s write() error: written (%s) != report length (%d)",
                             self.__name, written, len(report))
        except Exception as err:
            if isinstance(err, OSError) and (
                # https://github.com/raspberrypi/linux/commit/61b7f805dc2fd364e0df682de89227e94ce88e25
                err.errno == errno.EAGAIN  # pylint: disable=no-member
                or err.errno == errno.ESHUTDOWN  # pylint: disable=no-member
            ):
                logger.debug("HID-%s busy/unplugged (write): %s", self.__name, tools.efmt(err))
            else:
                logger.exception("Can't write report to HID-%s", self.__name)

        self.__state_flags.update(online=False)
        return False

    def __read_all_reports(self) -> None:
        if self.__noop or self.__read_size == 0:
            return

        assert self.__fd >= 0
        logger = get_logger()

        read = True
        while read:
            try:
                read = bool(select.select([self.__fd], [], [], 0)[0])
            except Exception as err:
                logger.error("Can't select() for read HID-%s: %s", self.__name, tools.efmt(err))
                break

            if read:
                try:
                    report = os.read(self.__fd, self.__read_size)
                except Exception as err:
                    if isinstance(err, OSError) and err.errno == errno.EAGAIN:  # pylint: disable=no-member
                        logger.debug("HID-%s busy/unplugged (read): %s", self.__name, tools.efmt(err))
                    else:
                        logger.exception("Can't read report from HID-%s", self.__name)
                else:
                    self._process_read_report(report)

    def __ensure_device(self) -> bool:
        if self.__noop:
            return True

        logger = get_logger()

        if self.__fd < 0:
            try:
                flags = os.O_NONBLOCK
                flags |= (os.O_RDWR if self.__read_size else os.O_WRONLY)
                self.__fd = os.open(self.__device_path, flags)
            except Exception as err:
                logger.error("Can't open HID-%s device %s: %s",
                             self.__name, self.__device_path, tools.efmt(err))

        if self.__fd >= 0:
            try:
                if select.select([], [self.__fd], [], self.__select_timeout)[1]:
                    # Закомментировано, потому что иногда запись доступна, но устройство отключено
                    # self.__state_flags.update(online=True)
                    return True
                else:
                    # Если запись недоступна, то скорее всего устройство отключено
                    logger.debug("HID-%s is busy/unplugged (write select)", self.__name)
            except Exception as err:
                logger.error("Can't select() for write HID-%s: %s", self.__name, tools.efmt(err))

        self.__state_flags.update(online=False)
        return False

    def __close_device(self) -> None:
        if self.__fd >= 0:
            try:
                os.close(self.__fd)
            except Exception:
                pass
            finally:
                self.__fd = -1
