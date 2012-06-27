"""
Main entry point for the script "objective-metadata-tool". This script is the public
entry point for this package.
"""
from __future__ import absolute_import
import argparse
import os
import glob
import platform
import ConfigParser
import subprocess

from . import compile
from . import merge_xml
from . import scan

parser = argparse.ArgumentParser(prog="objective-metadata-tool")
parser.add_argument("--verbose", action="store_true", help="print progress information", default=False)
parser.add_argument("--ini-file", metavar="FILE", help="configuration file", default="metadata/metadata.ini")
parser.add_argument("--ini-section", metavar="SECTION", action="append", default=[], help="section to use (default: use all sections)")

subparsers = parser.add_subparsers(dest="command")

scan_command = subparsers.add_parser("scan", help="Scan header files")
scan_command.add_argument("--sdk-root",
                help="Use the given SDK", metavar="DIR", default="/")
scan_command.add_argument("--arch", 
                help="Use the given processor architecture", metavar="ARCH", default="x86_64")

import_command = subparsers.add_parser("import", help="Import data from a bridgesupport file")
import_command.add_argument("--bridgesupport-file", metavar="FILE", help="Use this bridgesupport file")

compile_command = subparsers.add_parser("compile", help="Compile data into the PyObjC data file")


def make_pairs(sequence):
    sequence = iter(sequence)
    while True:
        first = sequence.next()
        second = sequence.next()
        yield (first, second)

def parse_ini(ini_file, ini_sections):
    ini_dir = os.path.dirname(ini_file)

    cfg = ConfigParser.ConfigParser()
    cfg.read([ini_file])
    if not ini_sections:
        ini_sections = cfg.sections()

    for section in ini_sections:
        info = {
                "framework": cfg.get(section, "framework"),
        }
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
            info["pre-headers"] = [x.strip() for x in cfg.get(section, "pre-headers").split(",")]
        else:
            info["pre-headers"] = []

        if cfg.has_option(section, "post-headers"):
            info["post-headers"] = [x.strip() for x in cfg.get(section, "post-headers").split(",")]
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
            info["compiled"] = os.path.join(ini_dir, "..", "Lib", info["python-package"].replace('.', os.path.sep), "_metadata.py")

        if cfg.has_option(section, "only-headers"):
            info["only-headers"] = [x.strip() for x in cfg.get(section, "only-headers").split(",")]
        else:
            info["only-headers"] = None

        if cfg.has_option(section, "DYLD_FRAMEWORK_PATH"):
            os.environ["DYLD_FRAMEWORK_PATH"] = cfg.get(section, "DYLD_FRAMEWORK_PATH")

        info["typemap"] = {}
        if cfg.has_option(section, "typemap"):
            for orig, new in make_pairs(cfg.get(section, "typemap").split()):
                info["typemap"][orig] = new
        yield info

def path_for_sdk_version(version):
    out =subprocess.check_output([
        "xcodebuild", "-version", "-sdk", "macosx%s"%(version,), "Path"])
    return out.strip()

def main():
    args = parser.parse_args()

    for info in parse_ini(args.ini_file, args.ini_section):
        if args.command == "scan":

            for arch in args.arch.split(','):
                arch = arch.strip()
                if not arch: continue

                for sdk in args.sdk_root.split(','):
                    sdk = sdk.strip()
                    if not sdk: continue

                    try:
                        float(sdk)
                    except ValueError:
                        pass

                    else:
                        sdk = path_for_sdk_version(sdk)

                    osver = sdk
                    if osver is None:
                        osver = platform.mac_ver()[0]
                    else:
                        osver = os.path.basename(osver)[6:-4]

                    raw_fn = os.path.join(info["raw"], "%s-%s.fwinfo"%(arch, osver))

                    if args.verbose:
                        print "Scan framework=%r sdk=%r arch=%r"%(
                                info["framework"], sdk, arch)
                    scan.scan_headers(raw_fn, 
                        info["exceptions"], 
                        info["framework"],
                        info["start-header"], 
                        info["pre-headers"], 
                        info["post-headers"], 
                        sdk, 
                        arch,
                        info["link-framework"],
                        info["only-headers"],
                        info["typemap"],
                    )

        elif args.command == "import":
            if args.verbose:
                print "Import framework=%r bridgesupport=%r"%(
                        info["framework"], args.bridgesupport_file)
            merge_xml.merge_xml(info["exceptions"], args.bridgesupport_file)

        elif args.command == "compile":
            if args.verbose:
                print "Compile framework=%r target=%r"%(
                        info["framework"], info["compiled"])
            compile.compile_metadata(info["compiled"], 
                    info["exceptions"], 
                    glob.glob(info["raw"] + "/*"), 
            )

        else:
            print "Internal error: Unimplemented command: %s"%(args.command,)
            sys.exit(1)
