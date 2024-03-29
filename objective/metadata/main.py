"""
Main entry point for the script "objective-metadata-tool". This script is the public
entry point for this package.
"""
from __future__ import absolute_import

import argparse
import configparser
import glob
import os
import platform
import re
import subprocess
import sys
import typing

from . import compile, docgen, protocols, scan, typestubs

parser = argparse.ArgumentParser(prog="objective-metadata-tool")
parser.add_argument(
    "--verbose", action="store_true", help="print progress information", default=False
)
parser.add_argument(
    "--ini-file",
    metavar="FILE",
    help="configuration file",
    default="metadata/metadata.ini",
)
parser.add_argument(
    "--ini-section",
    metavar="SECTION",
    action="append",
    default=[],
    help="section to use (default: use all sections)",
)

subparsers = parser.add_subparsers(dest="command")
scan_command = subparsers.add_parser("scan", help="Scan header files")
scan_command.add_argument(
    "--sdk-root", help="Use the given SDK", metavar="DIR", default="/"
)
scan_command.add_argument(
    "--libclang", help="Use libclang at path", metavar="FILE", default=None
)
scan_command.add_argument(
    "--min-deployment-ver", help="Use the given SDK", metavar="DIR", default=None
)
scan_command.add_argument(
    "--arch",
    help="Use the given processor architecture",
    metavar="ARCH",
    default="x86_64",
)

docgen_command = subparsers.add_parser("docgen", help="Generate a Sphinx module file")
docgen_command.add_argument(
    "--output-dir", help="Write the file is this directory", metavar="DIR", default=None
)

compile_command = subparsers.add_parser(
    "compile", help="Compile data into the PyObjC data file"
)
protocols_command = subparsers.add_parser(
    "protocols", help="Compile data into the protocols C file"
)
typestubs_command = subparsers.add_parser(
    "typestubs", help="Generate type stubs ('.pyi') for a framework"
)


def make_pairs(
    sequence: typing.Any,
) -> typing.Iterator[typing.Tuple[typing.Any, typing.Any]]:
    sequence = iter(sequence)
    while True:
        first = next(sequence)
        second = next(sequence)
        yield (first, second)


ConfigInfo = typing.TypedDict(
    "ConfigInfo",
    {
        "framework": str,
        "raw": str,  # make this a Path
        "exceptions": str,  # make this a Path
        "start-header": typing.Optional[str],
        "pre-headers": typing.List[str],
        "post-headers": typing.List[str],
        "link-framework": str,
        "compiled": str,  # make this a Path
        "typestub": str,  # make this a Path
        "protocols-file": str,  # make this a Path
        "only-headers": typing.Optional[typing.List[str]],
        "documentation-dir": str,  # make this a Path
        "python-package": str,
    },
    total=False,
)


def parse_ini(
    ini_file: typing.Union[str, os.PathLike], ini_sections: typing.Sequence[str]
) -> typing.Iterable[ConfigInfo]:  # incomplete type
    ini_dir = os.path.dirname(ini_file)

    cfg = configparser.ConfigParser()
    cfg.read([ini_file])
    if not ini_sections:
        ini_sections = cfg.sections()

    for section in ini_sections:
        info: ConfigInfo = {"framework": cfg.get(section, "framework")}

        if cfg.has_option(section, "raw"):
            info["raw"] = os.path.join(ini_dir, cfg.get(section, "raw"))
        else:
            info["raw"] = os.path.join(ini_dir, "raw")

        if cfg.has_option(section, "exceptions"):
            info["exceptions"] = os.path.join(ini_dir, cfg.get(section, "exceptions"))
        else:
            info["exceptions"] = os.path.join(ini_dir, info["framework"] + ".fwinfo")

        if cfg.has_option(section, "start-header"):
            info["start-header"] = cfg.get(section, "start-header")
        else:
            info["start-header"] = None

        if cfg.has_option(section, "pre-headers"):
            info["pre-headers"] = [
                x.strip() for x in cfg.get(section, "pre-headers").split(",")
            ]
        else:
            info["pre-headers"] = []

        if cfg.has_option(section, "post-headers"):
            info["post-headers"] = [
                x.strip() for x in cfg.get(section, "post-headers").split(",")
            ]
        else:
            info["post-headers"] = []

        if cfg.has_option(section, "link-framework"):
            info["link-framework"] = cfg.get(section, "link-framework")
        else:
            info["link-framework"] = info["framework"]

        if cfg.has_option(section, "python-package"):
            info["python-package"] = cfg.get(section, "python-package")
        else:
            info["python-package"] = info["framework"]

        if cfg.has_option(section, "compiled"):
            info["compiled"] = os.path.join(ini_dir, cfg.get(section, "compiled"))
        else:
            info["compiled"] = os.path.join(
                ini_dir,
                "..",
                "Lib",
                info["python-package"].replace(".", os.path.sep),
                "_metadata.py",
            )

        if cfg.has_option(section, "typestub"):
            info["typestub"] = os.path.join(ini_dir, cfg.get(section, "typestub"))
        else:
            info["typestub"] = os.path.join(
                ini_dir,
                "..",
                "Lib",
                info["python-package"].replace(".", os.path.sep),
                "__init__.pyi",
            )

        if cfg.has_option(section, "protocols-file"):
            info["protocols-file"] = os.path.join(
                ini_dir, cfg.get(section, "protocols-file")
            )
        else:
            info["protocols-file"] = os.path.join(
                ini_dir, "..", "Modules", "_" + info["framework"] + "_protocols.m"
            )

        if cfg.has_option(section, "only-headers"):
            info["only-headers"] = [
                x.strip() for x in cfg.get(section, "only-headers").split(",")
            ]
        else:
            info["only-headers"] = None

        if cfg.has_option(section, "DYLD_FRAMEWORK_PATH"):
            os.environ["DYLD_FRAMEWORK_PATH"] = cfg.get(section, "DYLD_FRAMEWORK_PATH")

        if cfg.has_option(section, "documentation-dir"):
            info["documentation-dir"] = os.path.join(
                ini_dir, cfg.get(section, "documentation-dir")
            )
        else:
            info["documentation-dir"] = os.path.join(ini_dir, "..", "docs")

        # info["typemap"] = {}
        # if cfg.has_option(section, "typemap"):
        #    for orig, new in make_pairs(cfg.get(section, "typemap").split()):
        #        info["typemap"][orig] = new
        yield info


def path_for_sdk_version(version: str) -> str:
    with open(os.devnull, "a") as dn:
        out = subprocess.check_output(
            ["xcodebuild", "-version", "-sdk", "macosx%s" % (version,), "Path"],
            stderr=dn,
        ).decode()
        return out.strip()


def sdk_ver_from_path(path: str) -> typing.Optional[float]:
    assert isinstance(path, str)
    with open(os.devnull, "a") as dn:
        process = subprocess.Popen(
            ["xcodebuild", "-version", "-sdk"], stderr=dn, stdout=-1
        )
        out_raw, unused_err = process.communicate()
        out = out_raw.decode()
        versions = out.split("SDKVersion: ")
        for ver in versions:
            if path in ver:
                ver_str = re.findall(r"[\d.]+", ver)[0].strip(".")
                try:
                    ver_flt = float(ver_str)
                    return ver_flt
                except ValueError:
                    pass
    return None


def main() -> None:
    args = parser.parse_args()

    if getattr(args, "libclang", None):
        from . import clang

        clang.Config.set_library_file(args.libclang)

    for info in parse_ini(args.ini_file, args.ini_section):
        if args.command == "scan":

            for arch in args.arch.split(","):
                arch = arch.strip()
                if not arch:
                    continue

                for sdk in args.sdk_root.split(","):
                    sdk = sdk.strip()
                    if not sdk:
                        continue

                    try:
                        float(sdk)
                    except ValueError:
                        pass
                    else:
                        sdk = path_for_sdk_version(sdk)

                    min_deploy = (
                        None
                        if args.min_deployment_ver is None
                        else float(str(args.min_deployment_ver))
                    )
                    if min_deploy is None:
                        current_os_ver = float(
                            ".".join(platform.mac_ver()[0].split(".")[0:2])
                        )
                        sdk_ver = sdk_ver_from_path(sdk)
                        assert sdk_ver is not None
                        min_deploy = min(current_os_ver, sdk_ver)

                    assert min_deploy is not None

                    osver = sdk
                    if osver is None:
                        osver = platform.mac_ver()[0]
                    else:
                        osver = os.path.basename(osver)[6:-4]

                    raw_fn = os.path.join(info["raw"], "%s-%s.fwinfo" % (arch, osver))

                    if args.verbose:
                        print(
                            "Scan framework=%r sdk=%r arch=%r"
                            % (info["framework"], sdk, arch)
                        )
                    scan.scan_headers(
                        raw_fn,
                        info["exceptions"],
                        info["framework"],
                        info["start-header"],
                        info["pre-headers"],
                        info["post-headers"],
                        sdk,
                        arch,
                        info["link-framework"],
                        info["only-headers"],
                        # info["typemap"],
                        min_deploy,
                        verbose=args.verbose,
                    )

        elif args.command == "compile":
            if args.verbose:
                print(
                    "Compile framework=%r target=%r"
                    % (info["framework"], info["compiled"])
                )
            compile.compile_metadata(
                info["compiled"], info["exceptions"], glob.glob(info["raw"] + "/*")
            )

        elif args.command == "typestubs":
            if args.verbose:
                print(
                    "Generate typestubs framework=%r target=%r"
                    % (info["framework"], info["compiled"])
                )
            typestubs.generate_typestubs(
                info["typestub"],
                info["framework"],
                info["exceptions"],
                glob.glob(info["raw"] + "/*"),
            )

        elif args.command == "protocols":
            protocols.compile_protocol_file(
                info["protocols-file"],
                info["exceptions"],
                glob.glob(info["raw"] + "/*"),
            )

        elif args.command == "docgen":
            docgen.generate_documentation(
                os.path.join(
                    args.output_dir
                    if args.output_dir is not None
                    else info["documentation-dir"],
                    info["framework"] + ".rst",
                ),
                info["framework"],
                info["exceptions"],
                glob.glob(info["raw"] + "/*"),
            )

        else:
            print("Internal error: Unimplemented command: %s" % (args.command,))
            sys.exit(1)
