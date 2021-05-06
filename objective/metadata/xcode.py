"""
Interaction with code

This code needs to be refactored
"""
import os
import re
import subprocess
import typing

from macholib.mach_o import CPU_TYPE_NAMES
from macholib.MachO import MachO


def macho_archs(filename: typing.Union[os.PathLike[str], str]) -> typing.Set[str]:
    result = set()

    m = MachO(os.fspath(filename))
    for hdr in m.headers:
        arch = CPU_TYPE_NAMES[hdr.header.cputype]
        if arch == "PowerPC":
            arch = "ppc"
        elif arch == "PowerPC64":
            arch = "ppc64"
        result.add(arch)

    return result


def path_for_default_sdk() -> str:
    with open(os.devnull, "a") as dn:
        out = subprocess.check_output(
            ["xcodebuild", "-version", "-sdk", "macosx", "Path"],
            stderr=dn,
        ).decode()
        return out.strip()


def path_for_sdk_version(version: str) -> str:
    with open(os.devnull, "a") as dn:
        out = subprocess.check_output(
            ["xcodebuild", "-version", "-sdk", "macosx%s" % (version,), "Path"],
            stderr=dn,
        ).decode()
        return out.strip()


def sdk_ver_from_path(
    path: typing.Union[os.PathLike[str], str]
) -> typing.Optional[typing.Tuple[int, int]]:
    with open(os.devnull, "a") as dn:
        path_str = os.fspath(path)
        process = subprocess.Popen(
            ["xcodebuild", "-version", "-sdk"], stderr=dn, stdout=-1
        )
        out_raw, unused_err = process.communicate()
        out = out_raw.decode()
        versions = out.split("SDKVersion: ")
        for ver in versions:
            if path_str in ver:
                ver_str = re.findall(r"[\d.]+", ver)[0].strip(".")
                try:
                    ver_tuple = tuple(int(x) for x in ver_str.split("."))
                    assert len(ver_tuple) == 2
                    return ver_tuple[0], ver_tuple[1]
                except ValueError:
                    pass
    return None


def archs_for_framework(
    framework_path: typing.Union[os.PathLike[str], str]
) -> typing.Set[str]:
    framework_name = os.path.splitext(os.path.basename(framework_path))[0]

    libname = os.path.join(framework_path, framework_name)
    if os.path.exists(libname):
        return macho_archs(libname)

    else:
        # Modern SDKs contain a YAML file with
        # information about the library.
        #
        # Note: These files appear to be YAM
        tbd_name = libname + ".tbd"

        found = None
        with open(tbd_name) as stream:
            for line in stream:
                if line.startswith("targets:"):
                    found = [line]

                elif found is not None and line and line[0].isspace():
                    found.append(line)

                elif found is not None:
                    break
        if found is None:
            raise RuntimeError(f"Don't know how to parse {tbd_name!r}")

        line = " ".join(found)
        _, _, value = line.partition(":")
        value = value.strip()
        assert value[0] == "[" and value[-1] == "]", repr(value)

        return {
            x.split("-")[0].strip()
            for x in value[1:-1].split(",")
            if x.strip().endswith("-macos")
        }
