# https://docs.platformio.org/en/latest/projectconf/advanced_scripting.html


from os.path import exists
from os.path import join
from os.path import basename

from typing import Dict

Import("env")


# =====
def _get_pkg_path(name: str) -> str:
    path = env.PioPlatform().get_package_dir(name)
    assert exists(path)
    return path


def _get_libs() -> Dict[str, str]:
    return {
        builder.name: builder.path
        for builder in env.GetLibBuilders()
    }


def _patch(path: str, patch_path: str) -> None:
    assert exists(path)
    flag_path: str = join(path, f".{basename(patch_path)}.done")
    if not exists(flag_path):
        env.Execute(f"patch -p1 -d {path} < {patch_path}")
        env.Execute(lambda *_, **__: open(flag_path, "w").close())


# =====
_patch(_get_pkg_path("framework-arduino-avr"), "patches/arduino-no-main.patch")
_patch(_get_pkg_path("framework-arduino-avr"), "patches/arduino-optional-cdc.patch")
_patch(_get_pkg_path("framework-arduino-avr"), "patches/arduino-get-plugged-endpoint.patch")

_libs = _get_libs()
_patch(_libs["HID-Project"], "patches/hid-shut-up.patch")
_patch(_libs["HID-Project"], "patches/hid-no-singletones.patch")
