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


from aiohttp.web import Request
from aiohttp.web import StreamResponse

from ....validators.basic import valid_bool
from ....validators.kvm import valid_log_seek

from ..logreader import LogReader

from ..http import exposed_http
from ..http import start_streaming


# =====
class LogApi:
    def __init__(self, log_reader: LogReader) -> None:
        self.__log_reader = log_reader

    # =====

    @exposed_http("GET", "/log")
    async def __log_handler(self, request: Request) -> StreamResponse:
        seek = valid_log_seek(request.query.get("seek", "0"))
        follow = valid_bool(request.query.get("follow", "false"))

        response = await start_streaming(request, "text/plain")

        async for record in self.__log_reader.poll_log(seek, follow):
            await response.write(("[%s %s] --- %s" % (
                record["dt"].strftime("%Y-%m-%d %H:%M:%S"),
                record["service"],
                record["msg"],
            )).encode("utf-8") + b"\r\n")

        return response
