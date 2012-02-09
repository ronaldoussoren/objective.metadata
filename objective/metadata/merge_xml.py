from __future__ import absolute_import
import sys
import pprint
import textwrap
from xml.etree import ElementTree

from . import storage

def bool_attr(node, key, default=False):
    if default:
        default = 'true'
    else:
        default = 'false'

    return (node.get(key, default) == 'true')

BOOLEAN_ATTRIBUTES=[
    ("already_retained", False),
    ("already_cfretained", False),
    ("c_array_length_in_result", False),
    ("c_array_delimited_by_null", False),
    ("c_array_of_variable_length", False),
    ("printf_format", False),
    ("free_result", False),
    ("null_accepted", True),
]

def typestr2typestr(value):
    # ".bridgesupport" files use "B" for objc._C_NSBOOL and "Z" for
    # objc._C_BOOL, which is different from what's used internally in
    # the bridge. 
    # Translate a bridgesupport typestring into one used by the bridge
    if value and value[0] in "onN^":
        return value[0] + typestr2typestr(value[1:])

    if value == "B":
        return "Z"
    elif value == "Z":
        return "B"

    # TODO: Handle compound types, not needed for now
    return value

def merge_xml(exceptions_fn, xml_fn):
    xml = ElementTree.parse(xml_fn)

    exceptions = storage.load_framework_info(exceptions_fn)

    update_classes(exceptions, xml)
    update_cftypes(exceptions, xml)
    update_protocols(exceptions, xml, 'informal_protocols')
    update_protocols(exceptions, xml, 'formal_protocols')
    update_functions(exceptions, xml)

    storage.save_framework_info(exceptions_fn, textwrap.dedent("""\
        # objective.metada exceptions file, see its document
        # for information on how to update this file.
        """), exceptions)

def update_functions(exceptions, xml):
    for funcname, funcdata in exceptions['definitions'].get('functions',{}).items():
        funcnode = xml.find(".//function[@name='%s']"%(funcname,))
        if funcnode is None:
            continue

        v = funcnode.get('suggestion')
        if v is not None:
            funcdata['suggestion'] = v

        if bool_attr(funcnode, 'variadic'):
            funcdata['variadic'] = True
            if bool_attr(funcnode, 'c_array_delimited_by_null'):
                funcdata['c_array_delimited_by_null'] = True

            v = funcnode.get('c_array_length_in_arg')
            if v is not None:
                funcdata['c_array_length_in_arg'] = int(v)

        argidx = 0
        for child in funcnode:
            if child.tag == 'retval':
                if 'retval' in funcdata:
                    parse_argnode(child, funcdata['retval'])
                    if 'typestr' in funcdata['retval']:
                        funcdata['retval']['type_override'] = funcdata['retval']['typestr']
                        del funcdata['retval']['typestr']

            elif child.tag == 'arg':
                if child.get('index') is not None:
                    argidx = int(child.get('index'))
                if 'args' in funcdata and argidx in funcdata['args']:
                    info = funcdata['args'][argidx]
                    parse_argnode(child, info)
                    if 'typestr' in info:
                        info['type_override'] = info['typestr']
                        del info['typestr']

                else:
                    info = {}
                    parse_argnode(child, info)
                    if 'typestr' in info:
                        del info['typestr']

                    if info:
                        if 'typestr' in info:
                            info['type_override'] = info['typestr']
                            del info['typestr']
                        if 'args' not in funcdata:
                            funcdata['args'] = {}
                            funcdata['args'][argidx] = info

                argidx += 1

            else:
                raise ValueError("Unexpected child %r of function"%(child.tag,))


def update_protocols(exceptions, xml, exception_key):
    nsobject_node = xml.find(".//class[@name='NSObject']")

    for protname, protdata in exceptions['definitions'].get(exception_key, {}).items():
        for method in protdata['methods']:
            methnode = locate_method(nsobject_node, method['selector'], method['class_method'])
            if methnode is not None:
                merge_method_info(method, methnode)

def update_cftypes(exceptions, xml):
    CFATTR=('tollfree', 'comment', 'ignore', 'gettypeid_func')
    for node in xml.findall('.//cftype'):
        for attr in CFATTR:
            if node.get(attr):
                break
        else:
            # No interesting information
            continue
        if 'cftypes' not in exceptions['definitions']:
            exceptions['definitions']['cftypes'] = {}

        try:
            info = exceptions['definitions']['cftypes'][node.get('name')]
        except KeyError:
            info = exceptions['definitions']['cftypes'][node.get('name')] = {}

        for attr in CFATTR:
            v = node.get(attr)
            if v is not None:
                info[attr] = v

def update_classes(exceptions, xml):
    if 0:
        for clsname, clsdata in exceptions['definitions'].get('classes', {}).items():
            clsnode = xml.find(".//class[@name='%s']"%(clsname,))
            if clsnode is None:
                continue

            for method in clsdata.get('methods', ()):
                methnode = locate_method(clsnode, method['selector'], method['class_method'])
                if methnode is not None:
                    merge_method_info(method, methnode)

    defs = exceptions['definitions'].get('classes', {})
    for clsnode in xml.findall(".//class"):
        clsname = clsnode.get('name')
        if clsname not in defs:
            clsdata = defs[clsname] = {'methods':[]}
        else:
            clsdata = defs[clsname]

        for methnode in clsnode:
            for node in clsdata.get('methods', ()):
                if (node['selector'] == methnode.get('selector')) and \
                        (node['class_method'] == bool_attr(methnode, 'class_method')):
                    merge_method_info(node, methnode)
                    break
            else:
                node = {
                    'selector': methnode.get('selector'),
                    'class_method': bool_attr(methnode, 'class_method'),
                }
                merge_method_info(node, methnode)
                clsdata['methods'].append(node)
            





def locate_method(root, selector, class_method):
    meth_nodes = root.findall(".//method[@selector='%s']"%(selector,))
    for methnode in meth_nodes:
        if class_method:
            if methnode.get('class_method', 'false') == 'true':
               return methnode
        else:
            if methnode.get('class_method', 'false') == 'false':
                return methnode
    return None

def merge_method_info(method, methnode):
    v = methnode.get('suggestion')
    if v is not None:
        method['suggestion'] = v

    if bool_attr(methnode, 'variadic'):
        method['variadic'] = True
        if bool_attr(methnode, 'c_array_delimited_by_null'):
            method['c_array_delimited_by_null'] = True

        v = methnode.get('c_array_length_in_arg')
        if v is not None:
            method['c_array_length_in_arg'] = int(v)

    for child in methnode:
        if child.tag == 'retval':
            try:
                info = method['retval']
            except KeyError:
                info = method['retval'] = {}
        elif child.tag == 'arg':
            idx = int(child.get('index'))
            try:
                info = method['args'][idx]
            except KeyError:
                if 'args' not in method:
                    method['args'] = {}
                info = method['args'][idx] = {}

        parse_argnode(child, info)

        # Rename the type node in exceptions, this makes it easier
        # to merge the exceptions into the full data without overwriting
        # the type information gathered from header files.
        if 'typestr' in info:
            info['type_override'] = info['typestr']
            del info['typestr']


def parse_argnode(child, info):
    for key in ['type_modifier']:
        v = child.get(key)
        if v is not None:
            info[key] = v

    for key in ['c_array_of_fixed_length']:
        v = child.get(key)
        if v is not None:
            info[key] = int(v)

    for key in ['sel_of_type', 'type']:
        v = child.get(key)
        v64 = child.get(key+'64')

        if key == 'type':
            key = 'typestr'
        if v is not None:
            if v64 is not None and v64 != v:
                info[key] = (typestr2typestr(v), typestr2typestr(v64))
            else:
                info[key] = typestr2typestr(v)
        elif v64 is not None:
            # Shouldn't actually happen, better not
            # loose information though
            info[key] = v64

    if bool_attr(child, 'function_pointer'):
        parse_callable(True, child, info)
    if bool_attr(child, 'block'):
        parse_callable(False, child, info)


    v = child.get('c_array_length_in_arg')
    if v is not None:
        if ',' in v:
            info['c_array_length_in_arg'] = [int(x) for x in v.split(',')]
        else:
            info['c_array_length_in_arg'] = int(v)

    for key, default in BOOLEAN_ATTRIBUTES:
        v = bool_attr(child, key, default)
        if v != default:
            info[key] = v

def parse_callable(isfunction, node, dct):
    if not bool_attr(node, 'function_pointer_retained', 'True'):
        dct['callable_retained'] = False

    meta = dct['callable'] = {}
    meta['arguments'] = arguments = {}
    idx = 0

    if not isfunction:
        # Blocks have an implicit first argument
        arguments[idx] = {
            'typestr': '^v',
        }
        idx += 1

    for child in node:
        if child.tag == 'retval':
            retval = meta['retval'] = {}
            parse_argnode(child, retval)

        elif child.tag == 'arg':
            arguments[idx] = {}
            parse_argnode(child, arguments[idx])
            idx += 1
        else:
            raise ValueError("Tag '%r' as child of function node"%(child.tag,))

    if meta.get('retval') is None:
        meta['retval'] = {
            'typestr': 'v',
        }
