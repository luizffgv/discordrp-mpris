import asyncio
import enum
import functools
import logging
from typing import Dict, Sequence, NamedTuple, TypeVar

import dbussy
import ravel

# type alias
ProxyInterface = ravel.BusPeer.Object.ProxyInterface

logger = logging.getLogger(__name__)


class PlayerInterfaces(NamedTuple):
    name: str
    root: ProxyInterface
    player: ProxyInterface
    tracklist: ProxyInterface = None
    playlists: ProxyInterface = None


class PlaybackStatus(str, enum.Enum):
    PLAYING = "Playing"
    PAUSED = "Paused"
    STOPPED = "Stopped"


_K = TypeVar('_K')
_V = TypeVar('_V', bound=Sequence)


def unwrap_metadata(metadata: Dict[_K, _V]) -> Dict[_K, _V]:
    return {k: v[1] for k, v in metadata.items()}


async def _list_bus_names(bus):
    dbus_obj = bus['org.freedesktop.DBus']['/org/freedesktop/DBus']
    # Could cache this, but only saves 0.25ms (30%)
    dbus_proxy = await dbus_obj.get_async_interface('org.freedesktop.DBus')
    return (await dbus_proxy.ListNames())[0]


# https://specifications.freedesktop.org/mpris-spec/2.2/
class Mpris2Dbussy():

    BUS_NAME = 'org.mpris.MediaPlayer2'
    PATH_NAME = '/org/mpris/MediaPlayer2'
    IFACE_NAME = 'org.mpris.MediaPlayer2'
    SUB_IFACES = ('Player', 'TrackList', 'Playlists')

    def __init__(self, bus, loop):
        if bus.loop is None:
            raise ValueError("Expected asynchronous bus")
        self.bus = bus
        self.loop = loop

    @classmethod
    async def create(cls, bus=None, loop=None):
        if not bus:
            bus = await ravel.session_bus_async(loop)

        return cls(bus, loop)

    async def get_player_names(self):
        bus_names = await _list_bus_names(self.bus)
        bus_names = [n for n in bus_names
                     if n.startswith(self.BUS_NAME + '.')]
        strip_len = len(self.BUS_NAME) + 1
        return [n[strip_len:] for n in bus_names]

    @functools.lru_cache()
    def get_player_object(self, name):
        return self.bus[f"{self.BUS_NAME}.{name}"][self.PATH_NAME]

    async def get_player_ifaces(self, name: str) -> PlayerInterfaces:
        # dbussy.DBusError: org.freedesktop.DBus.Error.ServiceUnknown
        #   -- The name org.mpris.MediaPlayer2.mpd was not provided by any .service files
        # dbussy.DBusError: org.freedesktop.DBus.Error.UnknownInterface
        #   -- Object does not implement the interface
        obj = self.get_player_object(name)
        iface_names = [self.IFACE_NAME,
                       *(f"{self.IFACE_NAME}.{sub}" for sub in self.SUB_IFACES)]
        coros = (obj.get_async_interface(if_name) for if_name in iface_names)
        results = await asyncio.gather(*coros, return_exceptions=True)
        args = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                if i < 2:  # required
                    raise result
                else:  # optional
                    result = None
            args.append(result)

        return PlayerInterfaces(name, *args)

    async def get_players(self):
        result_list = []
        for name in await self.get_player_names():
            try:
                result_list.append(await self.get_player_ifaces(name))
            except dbussy.DBusError as e:
                logger.error(f"Unable to fetch interfaces for player {name!r} - {e!s}")
                continue
        return result_list
