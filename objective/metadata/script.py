import sys
import optparse
import platform
import os
import objc
import pprint
from macholib.MachO import MachO
from macholib.mach_o import CPU_TYPE_NAMES

from . import parsing

opt_parser = optparse.OptionParser()
opt_parser.add_option("-f", "--framework", dest="framework",
        help="parse framework FRAMEWORK", metavar="FRAMEWORK")
opt_parser.add_option("--start-header", dest="start_header",
        help="use '#import <HEADER>' to load the framework", metavar="HEADER")
opt_parser.add_option("--sdk-root", dest="sdk_root",
        help="Use the given SDK", metavar="DIR", default="/")
opt_parser.add_option("--arch", dest="arch",
        help="Use the given processor architecture", metavar="ARCH", default="x86_64")
opt_parser.add_option("-o", "--ouput-file", dest="output",
        help="Write results to the file", metavar="FILE")
opt_parser.add_option("--pre-header", dest="preheaders", default=[], action="append",
        help="Include header before including framework headers", metavar="HEADER")


def macho_archs(filename):
    result = set()

    m = MachO(filename)
    for hdr in m.headers:
        arch = CPU_TYPE_NAMES[hdr.header.cputype]
        if arch == 'PowerPC':
            arch = 'ppc'
        elif arch == 'PowerPC64':
            arch = 'ppc64'
        result.add(arch)

    return result

def main():
    try:
        (options, args) = opt_parser.parse_args()
        if options.framework is None:
            print >>sys.stderr, "You have to pecify a framework"
            sys.exit(1)
        if options.output is None:
            fwk = options.sdk_root
            if fwk is None:
                fwk = platform.mac_ver()[0]
            else:
                fwk = os.path.basename(fwk)[6:-4]
            options.output = "info-files/%s-%s-%s.frwinfo"%(
                    options.framework, options.arch,  fwk)
        
        start_header = options.start_header
        if start_header is None:
            path = objc.dyld_framework('Headers', options.framework)

            file_archs = macho_archs(path)
            if options.arch not in file_archs:
                print >>sys.stderr, "Framework %r not available for arch %r"%(
                        options.framework, options.arch)
                sys.exit(1)


            path = os.path.dirname(path)
            path =  os.path.join(options.sdk_root, path[1:], 'Headers')
            if not os.path.exists(path):
                print >>sys.stderr, "Framework without headers"
                sys.exit(1)

            files = os.listdir(path)
            if len(files) == 1:
                start_header = '%s/%s'%(options.framework, files[0])

            else:
                if options.framework + '.h' not in files:
                    print >>sys.stderr, "Framework doesn't have a central header <%s/%s.h>"%(
                            options.framework, options.framework)
                    sys.exit(1)

        prs = parsing.FrameworkParser(
                options.framework, 
                start_header=start_header,
                sdk=options.sdk_root,
                arch=options.arch,
                preheaders=options.preheaders)
        prs.parse()

        if not os.path.exists(os.path.dirname(options.output)):
            os.makedirs(os.path.dirname(options.output))

        with open(options.output, 'w') as fp:
            pprint.pprint(prs.definitions(), stream=fp)

    except KeyboardInterrupt:
        sys.exit(1)
