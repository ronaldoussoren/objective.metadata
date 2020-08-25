import optparse
import os
import sys
import textwrap
import time
import typing

import objc
from macholib.mach_o import CPU_TYPE_NAMES
from macholib.MachO import MachO

from . import parsing, storage

opt_parser = optparse.OptionParser()
opt_parser.add_option(
    "-f",
    "--framework",
    dest="framework",
    help="parse framework FRAMEWORK",
    metavar="FRAMEWORK",
)
opt_parser.add_option(
    "--start-header",
    dest="start_header",
    help="use '#import <HEADER>' to load the framework",
    metavar="HEADER",
)
opt_parser.add_option(
    "--sdk-root", dest="sdk_root", help="Use the given SDK", metavar="DIR", default="/"
)
opt_parser.add_option(
    "--arch",
    dest="arch",
    help="Use the given processor architecture",
    metavar="ARCH",
    default="x86_64",
)
opt_parser.add_option(
    "-o",
    "--output-file",
    dest="output",
    help="Write results to the file",
    metavar="FILE",
)
opt_parser.add_option(
    "-e",
    "--exceptions-file",
    dest="exceptions",
    help="Write exceptions to the file",
    metavar="FILE",
)
opt_parser.add_option(
    "--pre-header",
    dest="preheaders",
    default=[],
    action="append",
    help="Include header before including framework headers",
    metavar="HEADER",
)
opt_parser.add_option(
    "--extra-header",
    dest="extraheaders",
    default=[],
    action="append",
    help="Include header after including the main framework header",
    metavar="HEADER",
)


def macho_archs(filename: str) -> typing.Set[str]:
    result = set()

    m = MachO(filename)
    for hdr in m.headers:
        arch = CPU_TYPE_NAMES[hdr.header.cputype]
        if arch == "PowerPC":
            arch = "ppc"
        elif arch == "PowerPC64":
            arch = "ppc64"
        result.add(arch)

    return result


def merge_meth_info(current, update):
    for a in update.get("args", ()):
        if a not in current.get("args", ()):
            if "args" not in current:
                current["args"] = {}
            current["args"][a] = update["args"][a]
        else:
            current["args"][a].update(update["args"][a])
    if "retval" in update:
        if "retval" in current:
            current["retval"].update(update["retval"])
        else:
            current["retval"] = update["retval"]


def locate_method(lst, sel):
    for item in lst:
        if item["selector"] == sel:
            return item
    return None


def locate_property(lst, name):
    for item in lst:
        if item["name"] == name:
            return item
    return None


def merge_exceptions(current, updates):
    for funcname, funcdata in updates["definitions"]["functions"].items():
        if funcname not in current["definitions"]:
            current["definitions"]["functions"][funcname] = funcdata

        else:
            merge_meth_info(
                current["definitions"]["functions"][funcname], updates[funcdata]
            )

    for section in ("formal_protocols", "informal_protocols", "classes"):
        for nm, info in updates["definitions"][section].items():
            if nm not in current["definitions"][section]:
                current["definitions"][section][nm] = info
            else:
                for meth in info.get("methods", ()):
                    m = locate_method(
                        current["definitions"][section][nm]["methods"], meth["selector"]
                    )
                    if m is None:
                        current["definitions"][section][nm]["methods"].append(meth)
                    else:
                        merge_meth_info(m, meth)

                for prop in info.get("properties", ()):
                    m = locate_property(
                        current["definitions"][section][nm]["properties"], prop["name"]
                    )
                    if m is None:
                        current["definitions"][section][nm]["properties"].append(prop)
                    else:
                        m.update(prop)

    return current


def scan_headers(
    raw_fn,
    exceptions_fn,
    framework,
    start_header,
    preheaders,
    extraheaders,
    sdk_root,
    arch,
    link_framework,
    only_headers,
    # typemap,
    min_deploy,
    verbose=False,
):
    if start_header is None:
        path = objc.dyld_framework("Headers", framework)

        file_archs = macho_archs(path)
        if arch not in file_archs:
            print(
                "Framework %r not available for arch %r" % (framework, arch),
                file=sys.stderr,
            )
            sys.exit(1)

        path = framework_path = os.path.dirname(path)
        path = os.path.join(sdk_root, path[1:], "Headers")
        if not os.path.exists(path):
            print(framework_path)
            if not os.path.exists(os.path.join(sdk_root, framework_path[1:])):
                path = os.path.join(framework_path, "Headers")
                if not os.path.exists(path):
                    print(
                        "Framework without headers[2]",
                        os.path.join(sdk_root, framework_path[1:]),
                        file=sys.stderr,
                    )
                    sys.exit(1)

            else:
                print("Framework without headers[1]", file=sys.stderr)
                sys.exit(1)

        files = os.listdir(path)
        if len(files) == 1:
            start_header = "%s/%s" % (framework, files[0])

        else:
            if framework + ".h" not in files:
                print(
                    "Framework doesn't have a central header <%s/%s.h>"
                    % (framework, framework),
                    file=sys.stderr,
                )
                sys.exit(1)

    prs = parsing.FrameworkParser(
        framework,
        start_header=start_header,
        sdk=sdk_root,
        arch=arch,
        preheaders=preheaders,
        extraheaders=extraheaders,
        link_framework=link_framework,
        only_headers=only_headers,
        # typemap=typemap,
        min_deploy=min_deploy,
        verbose=verbose,
    )

    prs.parse()

    if not os.path.exists(os.path.dirname(raw_fn)):
        os.makedirs(os.path.dirname(raw_fn))

    cur_time = time.ctime()
    storage.save_framework_info(
        raw_fn,
        textwrap.dedent(
            """\
        //             GENERATED FILE DO NOT EDIT
        //
        // This file was generated by objective.metadata
        // Last update: %s
        """
        )
        % (cur_time,),
        prs.definitions(),
    )

    new_exceptions = prs.exceptions
    if os.path.exists(exceptions_fn):
        cur_exceptions = storage.load_framework_info(exceptions_fn, verbose=verbose)
        new_exceptions = merge_exceptions(cur_exceptions, new_exceptions)

    storage.save_framework_info(
        exceptions_fn,
        textwrap.dedent(
            """\
        // objective.metadata exceptions file, see its document
        // for information on how to update this file.
        """
        ),
        new_exceptions,
        verbose=verbose,
    )
