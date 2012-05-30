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


class MergeNeededException (Exception):
    pass


HEADER=textwrap.dedent("""\
    # This file is generated by objective.metadata
    #
    # Last update: %(timestamp)s

    import objc, sys

    if sys.maxsize > 2 ** 32:
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

class ustr(object):
    def __init__(self, value):
        self._value = value

    def __repr__(self):
        value = self._value.encode('utf-8')
        return 'b%r.decode("utf-8")'%(value,)




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
            try:
                uniq.append((d[key], set([d['arch']])))
            except:
                raise

    if len(uniq) == 1:
        return {key: uniq[0][0]}

    elif len(uniq) == 2:
        value = classify_archs(uniq[0][1], uniq[1][1], uniq[0][0], uniq[1][0])
        if value is None:
            raise MergeNeededException('Merge needed %r'%(uniq,))

        return {key: value}

    else:
        raise MergeNeededException('Merge needed %r'%(uniq,))


def merge_definition_lists(defs):
    all_methods = {}
    for info in defs:
        arch = info['arch']
        methods = info['methods']
        for meth in methods:
            try:
                lst = all_methods[meth['selector']]
            except KeyError:
                lst = all_methods[meth['selector']] = []

            typestr = ''
            if 'retval' in meth:
                typestr = meth['retval']['typestr']
            else:
                typestr = 'v'
            typestr += '@:'
            for a in meth['args']:
                typestr += a['typestr']
            lst.append({'arch': arch, 'typestr': bstr(typestr)})

    result = []
    for selector, typestr in all_methods.items():
        for v in typestr:
            v['typestr'] = bstr(v['typestr'])
        typestr = merge_defs(typestr, 'typestr')['typestr']
        result.append(func_call('objc.selector')(None, bstr(selector), typestr, isRequired=False))

    return result


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
        return selector(None, bstr(meth['selector']), bstr(typestr), isRequired=False)

    for name in found:
        if len(found[name]) == 1:
            result[name] = informal_protocol(name, map(calc_selector, found[name][0]['methods']))

        else:
            # FIXME: This is too simple, need to actually merge the list of definitions
            merge_definition_lists(found[name])

            result[name] = informal_protocol(name, merge_definition_lists(found[name]))

    return result

def _cleanup_callable_metadata(metadata):
    def cleanup_type(rec):
        if 'typestr' in rec:
            rec['type'] = rec['typestr']
            del rec['typestr']
        elif 'type_override' in val:
            rec['type'] = rec['type_override']
            del rec['type_override']

        if isinstance(rec['type'], (list, tuple)):
            if isinstance(rec['type'][1], bool):
                # Correct scanner stores 'typestr_special' in wrong location
                rec['type'] = rec['type'][0]
            
        if isinstance(rec['type'], (list, tuple)):
            rec['type'] = sel32or64(*map(bstr, rec['type']))
        else:
            rec['type'] = bstr(rec['type'])
        return rec
   
    metadata['retval'] = cleanup_type(dict(metadata['retval']))

    d = {}
    if isinstance(metadata['args'], dict):
        metadata['args'] = [ metadata['args'][x] for x in range(len(metadata['args'])) ]
    for k, val in enumerate(metadata['args']):
        d[k] = cleanup_type(dict(val))

    metadata['arguments'] = d
    del metadata['args']

    return metadata


def calc_func_proto(exc, info, arch):
    types = []
    metadata = {}

    if info.get('variadic'):
        metadata['variadic'] = info['variadic']

    for k in exc:
        if k in ('args', 'retval'): 
            continue
        metadata[k] = exc[k]

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

    if 'function' in retval:
        retval['callable'] = _cleanup_callable_metadata(dict(retval['function']))
        del retval['function']

    metadata['arguments'] = {}


    arg_info = info['args']
    if isinstance(arg_info, dict):
        arg_info = [ arg_info[i] for i in range(len(arg_info)) ]

    for idx, a in enumerate(arg_info):

        # C has 'aFunction(void)' as the function prototype for functions
        # without arguments.
        if a.get('name') is None and a.get('typestr') == 'v': continue

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

        if 'sel_of_type' in arg:
            v = arg['sel_of_type']
            if isinstance(v, (list, tuple)):
                v = sel32or64(bstr(v[0]), bstr(v[1]))
            else:
                v = bstr(v)
            arg['sel_of_type'] = v

        if 'function' in arg:
            # XXX: This is suboptimal at best
            arg['callable'] = _cleanup_callable_metadata(dict(arg['function']))
            del arg['function']

        for k in arg:
            if isinstance(arg[k], list):
                arg[k] = tuple(arg[k])

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
            typestr, metadata = calc_func_proto(excinfo.get(name, {}), value, info['arch'])
            value = { 'typestr': typestr, 'metadata': metadata, 'arch': info['arch'] }
            functions[name] = [value]

    result = {}
    for name, value in functions.items():
        info = merge_defs(value, 'typestr')
        if value[0]['metadata']:
            result[name] = (info['typestr'], '', value[0]['metadata'])
        else:
            result[name] = (info['typestr'],)
    return result

def extract_opaque(exceptions, headerinfo):
    excinfo = exceptions['definitions'].get('opaque', {})

    opaque = {}
    createPointer = func_call("objc.createOpaquePointerType")
    for name, info in excinfo.items():
        if 'typestr' not in info:
            print "WARNING: Skip %r, no typestr found"%(name,)
            continue

        opaque[name] = createPointer(name, bstr(info['typestr']))

    return opaque



def extract_opaque_cftypes(exceptions, headerinfo):
    cftypes = {}
    excinfo = exceptions['definitions'].get('cftypes', {})

    for info in headerinfo:
        for name, value in info['definitions'].get('cftypes',{}).items():
            if name in excinfo:
                if excinfo[name].get('ignore', False): continue
                if not excinfo[name].get('opaque', False): continue
            else:
                # Not in exception data, cannot be 'opaque pointer'
                continue

            try:
                lst = cftypes[name]
            except KeyError:
                lst = cftypes[name] = []
                
            lst.append({'typestr': value['typestr'], 'arch':info['arch']})

    for name, value in excinfo.items():
        if name in cftypes: continue
        if 'typestr' not in value: continue
        if 'opaque' not in value: continue

        cftypes[name] = [{'typestr': value['typestr'], 'arch': info['arch']}]

    result = {}
    createPointer = func_call("objc.createOpaquePointerType")
    for name, values in sorted(cftypes.items()):
        if 'typestr' not in value:
            print "WARNING: Skip %r, no typestr found"%(name,)
            continue

        result[name] = createPointer(name, value['typestr'])

    return result

def extract_aliases(exceptions, headerinfo):
    aliases = {}
    excinfo = exceptions['definitions'].get('aliases', {})

    for info in headerinfo:
        for orig, alias in info['definitions'].get('aliases', {}).items():
            if orig in excinfo:
                if excinfo[orig].get('ignore', False): continue
                v = excinfo[orig].get('alias')
                if v is not None:
                    alias = v
            
            try:
                lst = aliases[orig]
            except KeyError:
                lst = aliases[orig] = []

            lst.append({'alias': alias, 'arch':info['arch']})

    result = {}
    for name, values in sorted(aliases.items()):
        alias = merge_defs(values, 'alias')['alias']

        result[name] = alias

    for name, value in excinfo.items():
        if name in result: continue
        if 'alias' not in value: continue
        if value.get('ignore', False): continue

        result[name] = value['alias']

    return result

def extract_cftypes(exceptions, headerinfo):
    cftypes = {}
    excinfo = exceptions['definitions'].get('cftypes', {})

    for info in headerinfo:
        for name, value in info['definitions'].get('cftypes',{}).items():
            if name in excinfo:
                if excinfo[name].get('ignore', False): continue
                if excinfo[name].get('opaque', False): continue

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
            (name, bstr(value['typestr']), exc.get('gettypeid_func'), exc.get('tollfree'))
        )

    for name, value in excinfo.items():
        if name in cftypes: continue
        if 'typestr' not in value: continue
        if 'opaque' in value: continue

        result.append(
            (name, bstr(value['typestr']), value.get('gettypeid_func'), value.get('tollfree'))
        )

    return result

def extract_expressions(exceptions, headerinfo):
    result = {}

    excinfo = exceptions['definitions'].get('expressions', {})

    # Add all definitions from parsed header files
    for info in headerinfo:
        for name, value in info['definitions'].get('expressions', {}).items():
            if name in excinfo:
                if excinfo[name].get('ignore', False): continue

            if name in result:
                result[name].append({'value': value, 'arch': info['arch']})

            else:
                result[name] = [{'value': value, 'arch': info['arch']}]


    # Finally add definitions that were manually added to  the exceptions file
    for name in excinfo:
        if name not in result and 'value' in excinfo[name]:
            result[name] = [{'typestr':excinfo[name]['value'], 'arch': None }]

    for name in result:
        result[name] = merge_defs(result[name], 'value')['value']

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
        if name not in result and 'type_override' in excinfo[name]:
            result[name] = [{'typestr':excinfo[name]['type_override'], 'arch': None }]
        if name not in result and 'typestr' in excinfo[name]:
            result[name] = [{'typestr':excinfo[name]['typestr'], 'arch': None }]


    for name in result:
        result[name] = merge_defs(result[name], 'typestr')
        if name in excinfo:
            if excinfo[name].get('magic_cookie', False):
                result[name]['magic_cookie'] = True


    return result

def extract_enums(exceptions, headerinfo):
    result = {}

    excinfo = exceptions['definitions'].get('enum', {})

    for info in headerinfo:
        for name, value in info['definitions'].get('enum', {}).items():
            if name in excinfo:
                if excinfo[name].get('ignore', False): continue
                if excinfo[name].get('value'):
                    if isinstance(excinfo[name]['value'], (str, unicode)):
                        result[name] = [{'value': bstr(excinfo[name]['value']), 'arch': None }]
                    else:
                        result[name] = [{'value': excinfo[name]['value'], 'arch': None }]
                    continue

                if excinfo[name].get('type') == 'unicode':
                    if name in result:
                        result[name].append({'value': unichr(value), 'arch': info['arch']})

                    else:
                        result[name] = [{'value': unichr(value), 'arch': info['arch']}]
                    continue

            if name in result:
                result[name].append({'value': value, 'arch': info['arch']})

            else:
                result[name] = [{'value': value, 'arch': info['arch']}]

    # Finally add definitions that were manually added to  the exceptions file
    for name in excinfo:
        if name not in result and 'value' in excinfo[name]:
            result[name] = [{'value':excinfo[name]['value'], 'arch': None }]

    for name in result:
        try:
            result[name] = merge_defs(result[name], 'value')
        except MergeNeededException:

            if name.endswith('Count'):
                # A number of headers define a kFooCount value that is
                # the highest support value of a specific kind, use
                # the maximum value in those cases.
                result[name] = max(x['value'] for x in result[name])
            else:
                raise

    return result


def exception_method(exceptions, key):
    for m in exceptions.get(key[0], {'methods':()}).get('methods', ()):
        if m['selector'] == key[1] and m['class_method'] == key[2]:
            return m
    return None


def merge_arginfo(current, update, arch, only_special):
    if 'typestr_special' in update:
        if update['typestr_special'] or not only_special:
            if 'type' not in current:
                current['type'] = collections.defaultdict(list)

            current['type'][update['typestr']].append(arch)

    for k in update:
        if k not in ('typestr', 'typestr_special'):
            current[k] = update[k]

def calc_type(choices):
    if isinstance(choices, str):
        # FIXME: investigate why this is needed (Collabortation wrappers)
        return choices
    if len(choices) == 1:
        return bstr(iter(choices).next())

    else:
        if isinstance(choices, list):
            return sel32or64(*choices)

        else:
            ch = []
            for k, v in choices.iteritems():
                for e in v:
                    ch.append({'value': bstr(k), 'arch': e})

            return merge_defs(ch, 'value')['value']

        raise ValueError("merge typestrings: %r"%(choices,))
        

def merge_method_info(clsname, selector, class_method, infolist, exception, only_special):
    """
    Merge method metadata and exceptions and return the resulting 
    information dictionary. Returns ``None`` when there is no information
    that couldn't be loaded at runtime by the bridge.
    """
    result = {
        'arguments': {}
    }
    for info in infolist:
        for k in info:
            if k in ('class', 'selector', 'class_method', 'arch'): 
                continue

            elif k == 'retval':
                if 'retval' not in result:
                    result['retval'] = {}

                merge_arginfo(result['retval'], info[k], info['arch'], only_special)

            elif k == 'args':
                for idx, value in enumerate(info[k]):
                    if idx+2 not in result['arguments']:
                        result['arguments'][idx+2] = {}

                    merge_arginfo(result['arguments'][idx+2], value, info['arch'], only_special)

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
                args = result['arguments']
                for idx, value in exception['args'].items():
                    if idx+2 in args:
                        args[idx+2].update(value)
                    else:
                        args[idx+2] = dict(value)

                    if 'type_override' in args[idx+2]:
                        args[idx+2]['type'] = args[idx+2]['type_override']
                        del args[idx+2]['type_override']

            else:
                result[k] = exception[k]
        
        for rec in itertools.chain([result.get('retval', {})], result.get('arguments', {}).values()):
            if 'c_array_length_in_arg' in rec:
                v = rec['c_array_length_in_arg']
                if isinstance(v, (list, tuple)):
                    input, output = v
                    input += 2
                    output += 2
                    v = input, output
                else:
                    v += 2
                rec['c_array_length_in_arg'] = v

            if 'callable' in rec:
                def replace_typestr(value):
                    if 'typestr' in value:
                        value['type'] = value['typestr']
                        del value['typestr']
                    for v in value.values():
                        if isinstance(v, dict):
                            replace_typestr(v)
                replace_typestr(rec['callable'])



    if 'retval' in result:
        if 'type' in result['retval']:
            result['retval']['type'] = calc_type(result['retval']['type'])

        for k in ('type_modifier', 'sel_of_type'):
            if k in result['retval']:
                result['retval'][k] = bstr(result['retval'][k])

        if 'callable' in result['retval']:
            callable = result['retval']['callable']
            for value in itertools.chain([callable.get('retval',{})], callable.get('arguments', {}).values()):
                if isinstance(value['type'], str):
                    value['type'] = bstr(value['type'])
                else:
                    value['type'] = sel32or64(bstr(value['type'][0]), bstr(value['type'][1]))


        if not result['retval']:
            del result['retval']

    if 'arguments' in result:
        for i, a in result['arguments'].items():
            if 'type' in a:
                a['type'] = calc_type(a['type'])

            for k in  ('type_modifier', 'sel_of_type'):
                if k in a:
                    if isinstance(a[k], (list, tuple)):
                        a[k] = sel32or64(bstr(a[k][0]), bstr(a[k][1]))
                    else:
                        a[k] = bstr(a[k])

            if 'callable' in a:
                callable = a['callable']
                for value in itertools.chain([callable.get('retval',{})], callable.get('arguments', {}).values()):
                    if 'type' not in value:
                        raise ValueError("%s %s"%(
                            infolist[0]['class'], infolist[0]['selector']))
                    if isinstance(value['type'], str):
                        value['type'] = bstr(value['type'])
                    else:
                        value['type'] = sel32or64(bstr(value['type'][0]), bstr(value['type'][1]))

            if not a:
                del result['arguments'][i]
        if not result['arguments']:
            del result['arguments']

    if not result:
        return None
    
    return {
        'class': clsname,
        'selector': selector,
        'class_method': class_method,
        'metadata': result,
    }

def extract_method_info(exceptions, headerinfo, section='classes'):
    result = {}
    excinfo = exceptions['definitions'].get("classes", {})

    for info in headerinfo:
        for name, value in info['definitions'].get(section, {}).items():
            for meth in value.get('methods', ()):
                key = (name, meth['selector'], meth['class_method'])
                if key in result:
                    result[key].append(dict(meth))
                else:
                    result[key] = [dict(meth)]

                result[key][-1]['arch'] = info['arch']
                result[key][-1]['class'] = name

            for prop in value.get('properties', ()):
                # Properties have a getter and optionally a setter method,
                # ensure that those are visible to the metadata system.
                getter = prop['name']
                setter = 'set' + getter[0].upper() + getter[1:] + ":"
                for item in prop.get('attributes', ()):
                    if item == 'readonly':
                        setter = None
                    elif item[0] == 'getter':
                        getter = item[1]
                    elif item[0] == 'setter':
                        setter = item[1]
                
                if getter:
                    key = (name, getter, False)
                    meth = {
                        "selector": getter,
                        "retval": {
                            "typestr": prop["typestr"],
                            "typestr_special": prop["typestr_special"],
                        },
                        "args": [],
                        "class_method": False,
                    }
                    if key in result:
                        result[key].append(dict(meth))
                    else:
                        result[key] = [dict(meth)]
                    result[key][-1]['arch'] = info['arch']
                    result[key][-1]['class'] = name

                if setter:
                    key = (name, setter, False)
                    meth = {
                        "selector": setter,
                        "retval": {
                            "typestr": "v",
                            "typestr_special": False
                        },
                        "args": [
                            {
                                "typestr": prop["typestr"],
                                "typestr_special": prop["typestr_special"],
                            },
                        ],
                        "class_method": False,
                    }
                    if key in result:
                        result[key].append(dict(meth))
                    else:
                        result[key] = [dict(meth)]
                    result[key][-1]['arch'] = info['arch']
                    result[key][-1]['class'] = name

    # XXX: copy data that's only in the exceptions file
    for key in list(result):
        if section != 'classes':
            use_key = ('NSObject',) + key[1:]
        else:
            use_key = key

        result[key] = merge_method_info(key[0], key[1], key[2], result[key], 
                exception_method(excinfo, use_key), section == 'classes')

    if section == 'classes':
        for clsname in excinfo:
            for meth in excinfo[clsname].get('methods', ()):
                key = (clsname, meth['selector'], meth['class_method'])
                if key in result:
                    continue

                result[key] = merge_method_info(clsname, meth['selector'], meth['class_method'], [], meth, section=='classes')

    result = [info for info in result.values() if info is not None]
    if section != 'classes':
        for item in result:
            item['class'] = 'NSObject'

    return result

def extract_structs(exceptions, headerinfo):
    excinfo = exceptions['definitions'].get('structs', {})
    createStructType = func_call('objc.createStructType')
    registerStructAlias = func_call('objc.registerStructAlias')
    getName = func_call('objc._resolve_name')

    structs = {}
    for info in headerinfo:
        for name, value in info['definitions'].get('structs', {}).items():
            if name in excinfo and excinfo[name].get('ignore', False):
                continue

            alias = None
            pack = None
            fieldnames = value['fieldnames']
            if name in excinfo:
                fieldnames = [(x) for x in excinfo[name].get('fieldnames', fieldnames)]
                alias = excinfo[name].get('alias', None)
                pack = excinfo[name].get('pack', None)

            if name not in structs:
                structs[name] = []

            structs[name].append({
                'typestr': value['typestr'],
                'fieldnames': fieldnames,
                'alias': alias,
                'pack': pack,
                'arch': info['arch'],
            })

    result = {}
    for name, values in structs.items():
        fieldnames = values[0]['fieldnames']
        for v in values:
            v['typestr'] = bstr(v['typestr'])
        typestr = merge_defs(values, 'typestr')['typestr']
        alias   = values[0]['alias']
        pack   = values[0]['pack']
        if fieldnames and isinstance(fieldnames[0], (list, tuple)):
            fieldnames = sel32or64(*map(str, fieldnames))
        else:
            fieldnames = map(str, fieldnames)

        if alias is None:
            if pack is None:
                result[name] = createStructType(name, typestr, fieldnames)
            else:
                result[name] = createStructType(name, typestr, fieldnames, None, pack)
        else:
            result[name] = registerStructAlias(typestr, getName(alias))


    return result

def emit_structs(fp, structs):
    if structs:
        print >>fp, "misc.update(%r)"%(structs,)


def emit_externs(fp, externs):
    result = []
    special = {}

    for k, v in sorted(externs.items()):
        if isinstance(v, dict):
            magic =  v.get('magic_cookie', False)

            if v['typestr'] == '@':
                result.append(k)

            elif isinstance(v['typestr'], _wrapped_call):
                special[k] = v['typestr']
            else:
                result.append('%s@%s%s'%(k, "=" if magic else "", v['typestr']))

        else:
            raise ValueError("manual mapping needed")

    fp.write("constants = '''$%s$'''\n"%(
        '$'.join(result),))
    if special:
        for k, v in special.items():
            fp.write("constants = constants + '$%s@%%s$'%%(%r,)\n"%(k, v))


def emit_enums(fp, enums):
    result = []
    expr = {}
    for k, v in sorted(enums.items()):
        if isinstance(v, dict):
            if isinstance(v['value'], _wrapped_call):
                expr[k] = v['value']

            elif isinstance(v['value'], unicode):
                expr[k] = ustr(v['value'])

            else:
                result.append('%s@%s'%(k, v['value']))

        elif isinstance(v, (int, long)):
            result.append('%s@%s'%(k, v))


        else:
            raise ValueError("manual mapping needed")

    fp.write("enums = '''$%s$'''\n"%(
        '$'.join(result),))
    if expr:
        fp.write('misc.update(%r)\n'%(expr,))


def emit_method_info(fp, method_info):
    if method_info:
        fp.write('r = objc.registerMetaDataForSelector\n')
        fp.write('objc._updatingMetadata(True)\n')
        fp.write('try:\n')

        for record in sorted(method_info, key=operator.itemgetter('class', 'selector')):
            fp.write("    r(%r, %r, %r)\n"%(bstr(record['class']), bstr(record['selector']), record['metadata']))

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

def emit_opaque(fp, opaque):
    if opaque:
        print >>fp, "misc.update(%r)"%(opaque,)

def extract_literal(exceptions, headerinfo):
    excinfo = exceptions['definitions'].get('literals', {})

    found = {}
    for info in headerinfo:
        for name, value in info['definitions'].get('literals', {}).items():
            if name in excinfo and excinfo[name].get('ignore', False):
                continue

            if name not in found:
                found[name] = []

            found[name].append({'value': value, 'arch':info['arch']})

    result = {}
    for k, v in found.items():
        v = merge_defs(v, 'value')
        result[k] = v['value']

    return result



def emit_literal(fp, literals):
    print >>fp, "misc.update(%r)"%(literals)

def emit_expressions(fp, expressions):
    print >>fp, "expressions = %r"%(expressions)

def emit_aliases(fp, aliases):
    if aliases:
        print >>fp, "aliases = %r"%(aliases,)

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

        emit_structs(fp, extract_structs(exceptions, headerinfo))
        emit_externs(fp, extract_externs(exceptions, headerinfo))
        emit_enums(fp, extract_enums(exceptions, headerinfo))
        emit_literal(fp, extract_literal(exceptions, headerinfo))
        emit_functions(fp, extract_functions(exceptions, headerinfo))
        emit_aliases(fp, extract_aliases(exceptions, headerinfo))
        emit_cftypes(fp, extract_cftypes(exceptions, headerinfo))
        emit_opaque(fp, extract_opaque_cftypes(exceptions, headerinfo))
        emit_opaque(fp, extract_opaque(exceptions, headerinfo))
        emit_method_info(fp, extract_method_info(exceptions, headerinfo))
        emit_method_info(fp, extract_method_info(exceptions, headerinfo, 'formal_protocols'))
        emit_method_info(fp, extract_method_info(exceptions, headerinfo, 'informal_protocols'))
        emit_informal_protocols(fp, extract_informal_protocols(exceptions, headerinfo))
        emit_expressions(fp, extract_expressions(exceptions, headerinfo))
        fp.write(FOOTER)
