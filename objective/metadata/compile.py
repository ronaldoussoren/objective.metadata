"""
Tool for merging an excptions file and the information collected from
a number of sets of header files (various architectures and SDKs) into
the metadata source file used by PyObjC.

The metadata source file is a python file with a number of definitions
that are used by the lazy loading functionality.
"""
from __future__ import absolute_import
import sys, glob, textwrap, time, itertools, collections, operator
from .storage import load_framework_info


HEADER=textwrap.dedent("""\
    # This file is generated by objective.metadata
    #
    # Last update: %(timestamp)s

    import objc, sys

    if sys.maxint > 2 ** 32:
        def sel32or64(a, b): return b
    else:
        def sel32or64(a, b): return a
    if sys.byteorder == 'little':
        def littleOrBig(a, b): return a
    else:
        def littleOrBig(a, b): return b

    misc = {
    }
""")

FOOTER=textwrap.dedent("""\

        # END OF FILE
""")

class bstr(str):
    def __repr__(self):
        return 'b' + super(bstr, self).__repr__()



class _wrapped_call (object):
    def __init__(self, name, args, kwds):
        self.name = name
        self.args = args
        self.kwds = kwds

    def __repr__(self):
        args = [repr(a) for a in self.args]
        for k, v in self.kwds.items():
            args.append('%s=%r'%(k, v))

        if args:
            return '%s(%s)'%(self.name, ", ".join(args))

class func_call (object):
    def __init__(self, func_name):
        self._func_name = func_name

    def __call__(self, *args, **kwds):
        return _wrapped_call(self._func_name, args, kwds)


sel32or64 = func_call('sel32or64')
littleOrBig = func_call('littleOrBig')


def _isLittleEndian(archs):
    return archs in (set(['i386']), set(['x86_64']), set(['i386', 'x86_64']))

def _isBigEndian(archs):
    return archs in (set(['ppc']), set(['ppc64']), set(['ppc', 'ppc64']))

def _is32Bit(archs):
    return archs in (set(['ppc']), set(['i386']), set(['ppc', 'i386']))

def _is64Bit(archs):
    return archs in (set(['ppc64']), set(['x86_64']), set(['ppc64', 'x86_64']))

def classify_archs(archs1, archs2, value1, value2):
    if _is32Bit(archs1) and _is64Bit(archs2):
        return sel32or64(value1, value2)
    elif _is32Bit(archs2) and _is64Bit(archs1):
        return sel32or64(value2, value1)
    elif _isLittleEndian(archs1) and _isBigEndian(archs2):
        return littleOrBig(value1, value2)
    elif _isLittleEndian(archs2) and _isBigEndian(archs1):
        return littleOrBig(value2, value1)
    else:
        return None

def merge_defs(defs, key):

    # Uniq is logically a dict mapping from a value
    # to the set of architectures that use this value.
    # This cannot be a real dictionary because we sometimes
    # the values used are not hashable.
    uniq = []
    for d in defs:
        for k, v in uniq:
            if k == d[key]:
                v.add(d['arch'])
                break
        else:
            uniq.append((d[key], set([d['arch']])))

    if len(uniq) == 1:
        return {key: uniq[0][0]}

    elif len(uniq) == 2:
        value = classify_archs(uniq[0][1], uniq[1][1], uniq[0][0], uniq[1][0])
        if value is None:
            raise ValueError('Merge needed')

        return {key: value}

    else:
        raise ValueError('Merge needed')


def extract_informal_protocols(exceptions, headerinfo):
    found = {}

    excinfo = exceptions['definitions'].get('informal_protocols', {})
    for info in headerinfo:
        for name, value in info['definitions'].get('informal_protocols', {}).items():
            if name in excinfo:
                if excinfo[name].get('ignore', False): continue
            
            if name not in found:
                found[name] = []

            found[name].append({ 'methods': value['methods'], 'arch': info['arch'] })

    informal_protocol = func_call('objc.informal_protocol')
    selector = func_call('objc.selector')
    result = {}

    def calc_selector(meth):
        typestr = ''
        if 'retval' in meth:
            typestr = meth['retval']['typestr']
        else:
            typestr = 'v'
        typestr += '@:'
        for a in meth['args']:
            typestr += a['typestr']
        return selector(None, meth['selector'], bstr(typestr), isRequired=False)

    for name in found:
        if len(found[name]) == 1:
            result[name] = informal_protocol(name, map(calc_selector, info[name][0]))

        else:
            result[name] = informal_protocol(name, map(calc_selector, merge_defs(found[name], 'methods')['methods']))

    return result

def calc_func_proto(exc, info, arch):
    types = []
    metadata = {}
    if 'retval' in exc and 'type_override' in exc['retval']:
        t = exc['retval']['type_override']
        if isinstance(t, (list, tuple)):
            if arch in ('i386', 'ppc'):
                types.append(t[0])
            else:
                types.append(t[1])
        else:
            types.append(t)
    elif 'retval' in info and 'typestr' in info['retval']:
        types.append(info['retval']['typestr'])
    else:
        types.append('v')

    retval = {}
    if 'retval' in info:
        retval.update(info['retval'])
    if exc and 'retval' in exc:
        retval.update(exc['retval'])
    if 'typestr' in retval:
        del retval['typestr']
    if 'type_override' in retval:
        del retval['type_override']
    if retval:
        metadata['retval'] = retval

    metadata['arguments'] = {}

    for idx, a in enumerate(info['args']):
        if 'args' in exc and 'type_override' in exc['args'].get(idx, {}):
            t = exc['args'][idx]['type_override']
            if isinstance(t, (list, tuple)):
                if arch in ('i386', 'ppc'):
                    types.append(t[0])
                else:
                    types.append(t[1])
            else:
                types.append(t)
        else:
            types.append(a['typestr'])

        arg = dict(a)
        if 'args' in exc and idx in exc['args']:
            arg.update(exc['args'][idx])
        if 'name' in arg:
            del arg['name']
        if 'typestr' in arg:
            del arg['typestr']
        if 'type_override' in arg:
            del arg['type_override']

        if arg:
            metadata['arguments'][idx] = arg

    if not metadata['arguments']:
        del metadata['arguments']

    return bstr(''.join(types)), metadata
        
def extract_functions(exceptions, headerinfo):
    functions = {}
    excinfo = exceptions['definitions'].get('functions', {})

    for info in headerinfo:
        for name, value in info['definitions'].get('functions',{}).items():
            if name in excinfo:
                if excinfo[name].get('ignore', False): continue


            typestr, metadata = calc_func_proto(excinfo.get(name, {}), value, info['arch'])
            value = { 'typestr': typestr, 'metadata': metadata, 'arch': info['arch'] }
            
            try:
                functions[name].append(value)
            except KeyError:
                functions[name] = [value]

    for name, value in excinfo.items():
        if name in functions: continue
        if value.get('retval') and value.get('args') is not None:
            functions[name] = [value]

    result = {}
    for name, value in functions.items():
        info = merge_defs(value, 'typestr')
        if value[0]['metadata']:
            result[name] = (info['typestr'], '', value[0]['metadata'])
        else:
            result[name] = (info['typestr'],)
    return result

def extract_cftypes(exceptions, headerinfo):
    cftypes = {}
    excinfo = exceptions['definitions'].get('cftypes', {})

    for info in headerinfo:
        for name, value in info['definitions'].get('cftypes',{}).items():
            if name in excinfo:
                if excinfo[name].get('ignore', False): continue

            try:
                lst = cftypes[name]
            except KeyError:
                lst = cftypes[name] = []
                
            lst.append({'typestr': value['typestr'], 'arch':info['arch']})

    result = []
    for name, values in sorted(cftypes.items()):
        value = merge_defs(values, 'typestr')
        exc = excinfo.get(name, {})

        result.append(
            (name, value, exc.get('gettypeid_func'), exc.get('tollfree'))
        )


    return result


def extract_externs(exceptions, headerinfo):
    result = {}

    excinfo = exceptions['definitions'].get('externs', {})

    # Add all definitions from parsed header files
    for info in headerinfo:
        for name, value in info['definitions'].get('externs', {}).items():
            if name in excinfo:
                if excinfo[name].get('ignore', False): continue
                if excinfo[name].get('type_override'):
                    values[name] = {'typestr': excinfo[name]['type_override'] }
                    continue

            typestr = value['typestr']
            if typestr == '^{__CFString}':
                typestr = '@'

            if name in result:
                result[name].append({'typestr': typestr, 'arch': info['arch']})

            else:
                result[name] = [{'typestr': typestr, 'arch': info['arch']}]


    # Finally add definitions that were manually added to  the exceptions file
    for name in excinfo:
        if name not in result and 'type_override' in excinfo['name']:
            result[name] = [{'typestr':excinfo['name']['type_override'], 'arch': None }]

    for name in result:
        result[name] = merge_defs(result[name], 'typestr')


    return result

def extract_enums(exceptions, headerinfo):
    result = {}

    excinfo = exceptions['definitions'].get('enum', {})

    for info in headerinfo:
        for name, value in info['definitions'].get('enum', {}).items():
            if name in excinfo:
                if excinfo[name].get('ignore', False): continue
                if excinfo[name].get('value'):
                    values[name] = {'value': excinfo[name]['value'] }
                    continue

            if name in result:
                result[name].append({'value': value, 'arch': info['arch']})

            else:
                result[name] = [{'value': value, 'arch': info['arch']}]

    # Finally add definitions that were manually added to  the exceptions file
    for name in excinfo:
        if name not in result and 'value' in excinfo['name']:
            result[name] = [{'value':excinfo['name']['value'], 'arch': None }]

    for name in result:
        result[name] = merge_defs(result[name], 'value')

    return result


def exception_method(exceptions, key):
    for m in exceptions.get(key[0], {'methods':()})['methods']:
        if m['selector'] == key[1] and m['class_method'] == key[2]:
            return m
    return None


def merge_arginfo(current, update, arch):
    if 'typestr_special' in update:
        if update['typestr_special']:
            if 'type' not in current:
                current['type'] = collections.defaultdict(list)

            current['type'][update['typestr']].append(arch)

    for k in update:
        if k not in ('typestr', 'typestr_special'):
            current[k] = update[k]

def calc_type(choices):
    if len(choices) == 1:
        return bstr(iter(choices).next())

    else:
        raise ValueError("merge typestrings")
        

def merge_method_info(infolist, exception):
    """
    Merge method metadata and exceptions and return the resulting 
    information dictionary. Returns ``None`` when there is no information
    that couldn't be loaded at runtime by the bridge.
    """
    result = {
        'args': {}
    }
    for info in infolist:
        for k in info:
            if k in ('class', 'selector', 'class_method', 'arch'): 
                continue

            elif k == 'retval':
                if 'retval' not in result:
                    result['retval'] = {}

                merge_arginfo(result['retval'], info[k], info['arch'])

            elif k == 'args':
                for idx, value in enumerate(info[k]):
                    if idx not in result['args']:
                        result['args'][idx] = {}

                    merge_arginfo(result['args'][idx], value, info['arch'])

            elif k == 'visibility':
                pass

            else:
                # merge basic attributes, for now all entries
                # should have the same information.
                if k in result:
                    if result[k] != info[k]:
                        raise ValueError(k)
                else:
                    result[k] = info[k]

    if exception is not None:
        # Merge exception information
        for k in exception:
            if k in ('selector', 'class_method'):
                continue

            if k == 'retval':
                if 'retval' in result:
                    result['retval'].update(exception[k])
                else:
                    result['retval'] = dict(exception[k])

                if 'type_override' in result['retval']:
                    result['retval']['type'] = result['retval']['type_override']
                    del result['retval']['type_override']

            elif k == 'args':
                args = result['args']
                for idx, value in exception['args'].items():
                    if idx in args:
                        args[idx].update(value)
                    else:
                        args[idx] = dict(value)

                    if 'type_override' in args[idx]:
                        args[idx]['type'] = args[idx]['type_override']
                        del args[idx]['type_override']

            else:
                info[k] = exception[k]
        pass

    if 'retval' in result:
        if 'type' in result['retval']:
            result['retval']['type'] = calc_type(result['retval']['type'])

        for k in ('type_modifier', 'sel_of_type'):
            if k in result['retval']:
                result['retval'][k] = bstr(result['retval'][k])

        if not result['retval']:
            del result['retval']

    if 'args' in result:
        for i, a in result['args'].items():
            if 'type' in a:
                a['type'] = calc_type(a['type'])

            for k in  ('type_modifier', 'sel_of_type'):
                if k in a:
                    a[k] = bstr(a[k])

            if not a:
                del result['args'][i]
        if not result['args']:
            del result['args']

    if not result:
        return None
    return {
        'class': infolist[0]['class'],
        'selector': infolist[0]['selector'],
        'class_method': infolist[0]['class_method'],
        'metadata': result,
    }

def extract_method_info(exceptions, headerinfo):
    result = {}
    excinfo = exceptions['definitions'].get('classes', {})

    for info in headerinfo:
        for name, value in info['definitions'].get('classes', {}).items():
            for meth in value.get('methods', ()):
                key = (name, meth['selector'], meth['class_method'])
                if key in result:
                    result[key].append(dict(meth))
                else:
                    result[key] = [dict(meth)]

                result[key][-1]['arch'] = info['arch']
                result[key][-1]['class'] = name

    for key in list(result):
        result[key] = merge_method_info(result[key], 
                exception_method(excinfo, key))

    return [info for info in result.values() if info is not None]



def emit_externs(fp, externs):
    result = []
    for k, v in sorted(externs.items()):
        if isinstance(v, dict):
            if v['typestr'] == '@':
                result.append(k)
            else:
                result.append('%s@%s'%(k, v['typestr']))

        else:
            raise ValueError("manual mapping needed")

    fp.write("constants = '''$%s$'''\n"%(
        '$'.join(result),))

def emit_enums(fp, enums):
    result = []
    for k, v in sorted(enums.items()):
        if isinstance(v, dict):
            result.append('%s@%s'%(k, v['value']))

        else:
            raise ValueError("manual mapping needed")

    fp.write("enums = '''$%s$'''\n"%(
        '$'.join(result),))


def emit_method_info(fp, method_info):
    if method_info:
        fp.write('r = objc.registerMetaDataForSelector\n')
        fp.write('objc._updatingMetadata(True)\n')
        fp.write('try:\n')

        for record in sorted(method_info, key=operator.itemgetter('class', 'selector')):
            fp.write("    r(%r, %r, %r)\n"%(record['class'], record['selector'], record['metadata']))

        fp.write('finally:\n')
        fp.write('    objc._updatingMetadata(False)\n')

def emit_informal_protocols(fp, protocol_info):
    if protocol_info:
        print >>fp, "protocols=%r"%(protocol_info,)

def emit_functions(fp, functions):
    if functions:
        print >>fp, "functions=%r"%(functions,)

def emit_cftypes(fp, cftypes):
    if cftypes:
        print >>fp, "cftypes=%r"%(cftypes,)

def compile_metadata(output_fn, exceptions_fn, headerinfo_fns):
    """
    Combine the data from header files scans and manual exceptions
    into a file than is usable for the metadata support in 
    pyobjc 2.4 or later.
    """
    exceptions = load_framework_info(exceptions_fn)
    headerinfo = [load_framework_info(fn) for fn in headerinfo_fns]
    with open(output_fn, 'w') as fp:
        fp.write(HEADER % dict(timestamp=time.ctime()))

        emit_externs(fp, extract_externs(exceptions, headerinfo))
        emit_enums(fp, extract_enums(exceptions, headerinfo))
        #emit_strconst(fp, extract_strconst(exceptions, headerinfo))
        emit_functions(fp, extract_functions(exceptions, headerinfo))
        # functions
        # cftype
        emit_cftypes(fp, extract_cftypes(exceptions, headerinfo))
        emit_method_info(fp, extract_method_info(exceptions, headerinfo))
        emit_informal_protocols(fp, extract_informal_protocols(exceptions, headerinfo))
        # null_const

        fp.write(FOOTER)
