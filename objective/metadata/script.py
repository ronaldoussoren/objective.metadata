import sys
import optparse

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

def main():
    (options, args) = opt_parser.parse_args()

    prs = parsing.FrameworkParser(
            options.framework, 
            start_header=options.start_header,
            sdk=options.sdk_root,
            arch=options.arch)
    prs.parse()

    import pprint
    pprint.pprint(prs.definitions())
