"""
Utility module for parsing the header files in a framework and extracting 
interesting definitions.
"""
import operator
import os
import subprocess
import platform
import re
import sys

import objc


from typecodes import TypeCodes
from ast_tools import parse_int, constant_fold

from objective.cparser import parse_file, c_ast

LINE_RE=re.compile(r'^# \d+ "([^"]*)" ')
DEFINE_RE=re.compile(r'#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.*)$')
INT_RE=re.compile('^\(?(?:\([A-Za-z][A-Za-z0-9]*(?:\s+long)?\))?((?:-?0[Xx][0-9A-Fa-f]+)|(?:-?\d+))[UL]*\)?$')
FLOAT_RE=re.compile('^\(?(?:\([A-Za-z][A-Za-z0-9]*\))?\(?([-]?\d+\.\d+)\)?\)?$')
STR_RE=re.compile('^"(.*)"$')
UNICODE_RE=re.compile('^@"(.*)"$')
UNICODE2_RE=re.compile('^CFSTR\("(.*)"\)$')
ALIAS_RE=re.compile('^(?:\(\s*[A-Za-z0-9_]+\s*\))?\s*([A-Za-z_][A-Za-z0-9_]*)$')
NULL_VALUE=re.compile(r'\(\([A-Za-z0-9]+\)NULL\)')
CALL_VALUE=re.compile(r'^[A-Za-z0-9]+\([A-Za-z0-9]*\)$')

FUNC_DEFINE_RE=re.compile(r'#\s*define\s+([A-Za-z_][A-Za-z0-9_]*\([A-Za-z0-9_, ]*\))\s+(.*)$')
ERR_SUB_DEFINE_RE=re.compile(r'err_sub\s*\(\s*((?:0x)?[0-9a-fA-F]+)\s*\)')
ERR_SYSTEM_DEFINE_RE=re.compile(r'err_system\s*\(\s*((?:0x)?[0-9a-fA-F]+)\s*\)')
SC_SCHEMA_RE=re.compile(r"SC_SCHEMA_KV\s*\(\s*([A-Za-z0-9_]*)\s*,.*\)")
OR_EXPR_RE=re.compile(r"\([A-Za-z0-9]*(\s*\|\s*[A-Za-z0-9]*)*\)")


class FilteredVisitor (c_ast.NodeVisitor):
    """ 
    A node visitor that will only call the visit_* 
    methods for nodes that should be selected according
    to a framework parser
    """
    def __init__(self, parser):
        self._parser = parser

    def visit(self, node):
        if not self._parser._select_node(node):
            self.generic_visit(node)
            return

        #if node.coord is not None and 'ABAddressBook.h' in node.coord.file:
        #    print node, node.coord.file
        #    try:
        #        node.show(showcoord=True, attrnames=True)
        #    except AttributeError:
        #        pass

        super(FilteredVisitor, self).visit(node)

class DefinitionVisitor (FilteredVisitor):
    """
    A NodeVisitor that calls back to the framework parser when it
    locates interesting definitions.
    """
    def visit_Protocol(self, node):
        self.generic_visit(node)

        self._parser.add_protocol(node)

    def visit_EnumeratorList(self, node):
        self.generic_visit(node)
        self._parser.append_enumerator_list(node)

    def visit_Typedef(self, node):
        self.generic_visit(node)

        self._parser.typedefs[node.name] = node.type

        if isinstance(node.type, c_ast.FuncDecl):
            return

        if not getattr(node.type, "quals", None):
            if isinstance(node.type, c_ast.TypeDecl):
                if node.type.declname in self._parser.cftypes:
                    self._parser.aliases[node.name] = node.type.declname

            if isinstance(node.type.type, c_ast.Struct):
                self._parser.add_struct(node.name, node.type.type)

            if isinstance(node.type, c_ast.PtrDecl):
                if isinstance(node.type.type.type, c_ast.Struct):
                    stp = node.type.type.type
                    if stp.name is not None and stp.name.startswith('__'):
                        if stp.decls is None:
                            self._parser.add_cftype(node.name, node.type)

    def visit_Decl(self, node):
        self.generic_visit(node)
        if node.name is None:
            return

        if 'extern' in node.storage:
            if isinstance(node.type, c_ast.PtrDecl):
                self._parser.add_extern(node.name, node.type)

            elif isinstance(node.type, c_ast.TypeDecl):
                self._parser.add_extern(node.name, node.type)

        if isinstance(node.type, c_ast.FuncDecl):
            self._parser.add_function(node.name, node.type, node.funcspec)


    def visit_Category(self, node):
        self.generic_visit(node)

        if node.name == 'NSObject' and node.categorie_name: #XXX
            self._parser.add_informal_protocol(node)

        self._parser.add_category(node)

    def visit_Interface(self, node):
        self.generic_visit(node)
        self._parser.add_interface(node)

class FrameworkParser (object):
    """
    Parser for framework headers. 
    
    This class uses objective.cparser to to the actual work and stores
    all interesting information found in the headers.
    """
    def __init__(self, framework, arch='x86_64', sdk='/', start_header=None, preheaders=(), extraheaders=(), link_framework=None, only_headers=None, typemap=None):
        self.framework = framework
        self.link_framework = link_framework if link_framework is not None else framework
        self.framework_path = '/%s.framework/'%(framework,)
        if start_header is not None:
            self.start_header = start_header

        else:
            self.start_header = '%s/%s.h'%(framework, framework)

        self.only_headers = only_headers

        self.preheaders = preheaders
        self.extraheaders = extraheaders
        self.additional_headers = []
        self.arch = arch
        self.sdk = sdk
        self.typemap = typemap
        
        self.headers = set()

        self.enum_values= {}
        self.structs = {}
        self.externs = {}
        self.literals = {}
        self.aliases = {}
        self.expressions = {}
        self.functions = {}
        self.cftypes = {}
        self.func_macros = {}
        self.typedefs = {}
        self.formal_protocols = {}
        self.informal_protocols = {}
        self.classes = {}
        self.called_definitions = {}
        self._func_protos = {}
        self._init_func_protos()


    def _init_func_protos(self):
        self._func_protos['CFComparatorFunction'] = {
            # This function typedef has an prototype that would
            # require manual annotation, tweak the prototype to
            # be more useful
            'retval': { 'typestr': objc._C_ULNG },
            'args': [
                { 'typestr': objc._C_ID },
                { 'typestr': objc._C_ID },
                { 'typestr': objc._C_ID },
            ]
        }



    def _gen_includes(self, fp):
        for hdr in self.preheaders:
            fp.write('#import <%s>\n'%(hdr,))
        fp.write('#import <%s>\n'%(self.start_header,))
        for hdr in self.additional_headers:
            fp.write('#import <%s/%s>'%(self.framework, hdr))
        for hdr in self.extraheaders:
            fp.write('#import <%s>\n'%(hdr,))


    def parse(self):

        # - Generate a temporary file that #imports the framework
        #   We need to create the file because we need to use the 
        #   preprocessor
        fname = '_prs_%s.m'%(self.framework,)
        with open(fname, 'w') as fp:
            self._gen_includes(fp)

        # - Parse the file. 
        #   The -D and -U options are needed to strip out bits of code
        #   that are not yet supported by objective.cparser
        try:
            ast = parse_file(fname, 
                use_cpp=True, cpp_args=[
                    '-arch', self.arch, '-isysroot', self.sdk,
                    '-E', '-arch', self.arch, '-D__attribute__(x)=',
                    '-D__typeof__(x)=long',], cpp_path='clang')

            self.parse_defines(fname)
        finally:
            os.unlink(fname)

        self.typecodes = TypeCodes(self.arch, self.typemap)
        self.typecodes.fill_from_ast(ast)
        
        # - And finally walk the AST to find useful definitions
        visitor = DefinitionVisitor(self)
        visitor.visit(ast)

    def definitions(self):
        """
        Returns a dictionary with information about what was parsed and
        the definitions.
        """
        return {
            'framework':    self.framework,
            'arch':         self.arch,
            'sdk':          self.sdk,
            'release':      platform.mac_ver()[0],

            'headers':      list(sorted(self.headers)),

            'definitions': {
                'enum':      self.enum_values,
                'structs':   self.structs,
                'externs':   self.externs,
                'literals':  self.literals,
                'aliases':   self.aliases,
                'expressions':   self.expressions,
                'functions': self.functions,
                'cftypes':   self.cftypes,
                'func_macros': self.func_macros,
                'formal_protocols': self.formal_protocols,
                'informal_protocols': self.informal_protocols,
                'classes': self.classes,
                'called_definitions': self.called_definitions,
            },
        }

    def exceptions(self):
        functions = {}
        for nm, definition in self.functions.items():
            info = {}
            for idx, a in enumerate(definition['args']):
                if a['typestr'].startswith('^'):
                    a = dict(a)
                    del a['typestr']
                    del a['name']
                    try:
                        info['args'][idx] = a
                    except KeyError:
                        info['args'] = { idx: a }

            if definition['retval']['typestr'].startswith('^'):
                a = dict(definition['retval'])
                del a['typestr']
                info['retval'] = a

            if definition.get('variadic', False):
                info['variadic'] = True

            if info:
                functions[nm] = info

        prots = {}
        for section in ('formal_protocols', 'informal_protocols'):
            prots[section] = {}
            for prot, protdef in getattr(self, section).items():
                for meth in protdef['methods']:
                    info = {}
                    for idx, a in enumerate(meth['args']):
                        if a['typestr'].startswith('^'):
                            a = dict(a)
                            del a['typestr']
                            del a['typestr_special']
                            try:
                                info['args'][idx] = a
                            except KeyError:
                                info['args'] = {idx: a}

                    if meth['retval']['typestr'].startswith('^'):
                        a = dict(meth['retval'])
                        del a['typestr']
                        del a['typestr_special']
                        info['retval'] = a

                    if meth.get('variadic', False):
                        info['variadic'] = True


                    if info:
                        if prot not in prots[section]:
                            prots[section][prot] = {}
                        if 'methods' not in prots[section][prot]:
                            prots[section][prot]['methods'] = []

                        info['selector'] = meth['selector']
                        info['class_method'] = meth['class_method']
                        prots[section][prot]['methods'].append(info)
                meth = None

                for prop in protdef['properties']:
                    if prop['typestr'].startswith('^'):
                        if prot not in formal_protocols:
                            prots[section][prot] = {}
                        if 'properties' not in prots[section][prot]:
                            prots[section][prot]['properties'] = []

                        prop = dict(prop)
                        del prop['typestr']
                        del prop['typestr_special']
                        prots[section][prot]['properties'].append(prop)
                prop = None

        classes = {}
        for clsname, clsdef in self.classes.items():
            for method in clsdef['methods']:
                info = {}
                for idx, a in enumerate(method['args']):
                    if a['typestr'].startswith('^'):
                        a = dict(a)
                        del a['typestr']
                        del a['typestr_special']
                        try:
                            info['args'][idx] = a
                        except KeyError:
                            info['args'] = { idx: a }

                if method['retval']['typestr'].startswith('^'):
                    a = dict(method['retval'])
                    del a['typestr']
                    del a['typestr_special']
                    info['retval'] = a

                if method.get('variadic', False):
                    info['variadic'] = True

                if info:
                    if clsname not in classes:
                        classes[clsname] = {}
                    if 'methods' not in classes[clsname]:
                        classes[clsname]['methods'] = []

                    info['selector'] = method['selector']
                    info['class_method'] = method['class_method']
                    classes[clsname]['methods'].append(info)

            for prop in clsdef['properties']:
                if prop['typestr'].startswith('^'):
                    if clsname not in classes:
                        classes[clsname] = {}
                    if 'properties' not in classes[clsname]:
                        classes[clsname]['properties'] = []

                    prop = dict(prop)
                    del prop['typestr']
                    del prop['typestr_special']
                    classes[clsname]['properties'].append(prop)


        return {
            'definitions': {
                'functions': functions,
                'formal_protocols': prots['formal_protocols'],
                'informal_protocols': prots['informal_protocols'],
                'classes': classes,
            },
        }

    def _select_node(self, node):
        """ 
        Return True iff ``node`` is an AST node that's loaded from a 
        header for the current framework.
        """
        if not isinstance(node, c_ast.Node):
            return False

        if node.coord is None:
            return False

        if node.coord.file is None:
            return False

        if self.framework_path in node.coord.file:
            i = node.coord.file.index(self.framework_path)
            p = node.coord.file[i+len(self.framework_path) +len('Headers/'):]

            if self.only_headers:
                if os.path.basename(p) not in self.only_headers:
                    return False
             
            self.headers.add(p)
            return True

        return False

    def _calculate_enum_value(self, name):
        fname = '_prs_%s.m'%(self.framework,)
        with open(fname, 'w') as fp:
            self._gen_includes(fp)

            fp.write("#include <stdio.h>\n")
            fp.write("int main(void) {\n")
            fp.write("   printf(\"%%lld\\n\", (long long)%s);\n"%(name,))
            fp.write("   return 0;\n")
            fp.write("}\n")

        p = subprocess.Popen(['clang', 
            '-o', fname[:-2], 
            '-arch', self.arch,
            '-isysroot', self.sdk,
            fname,
            '-framework', self.link_framework])
        xit = p.wait()
        os.unlink(fname)
        if xit != 0:
            print "WARNING: Cannot calculate value for '%s'"%(name,)
            return None

        p = subprocess.Popen(['./' + fname[:-2]], stdout=subprocess.PIPE)
        data = p.communicate()[0]
        xit = p.wait()
        os.unlink(fname[:-2])
        if xit != 0:
            print "WARNING: Cannot calculate value for '%s'"%(name,)
            return None


        return int(data.strip())

    def append_enumerator_list(self, node):

        prev_name = None
        prev_value = None
        for item in node.children():

            value = item.value
            if value is not None:
                value = constant_fold(value, self.enum_values)

            if value is None:
                if prev_value is not None:
                    value = prev_value + 1

                elif prev_name is not None:
                    value = c_ast.BinaryOp(
                        '+', prev_name, c_ast.Constant('int', 1))

                else:
                    value = 0

            elif isinstance(value, c_ast.Constant) and value.type == 'int':
                if value.value.startswith('!'):
                    value = parse_int(value.value[1:])
                    if value:
                        value = 0
                    else:
                        value = 1
                else:
                    value = parse_int(value.value)

            prev_name = item.name
            if isinstance(value, int):
                prev_value = value
            else:
                prev_value = None

                value = self._calculate_enum_value(item.name)
                if value is None:
                    continue

            self.enum_values[item.name] = value

    def add_cftype(self, name, type):
        typestr, _ = self.typecodes.typestr(type)
        self.cftypes[name] = {
            'typestr': typestr,
        }

    def add_struct(self, name, type):
        if name in self.typecodes:
            typestr = self.typecodes[name]
            special = self.typecodes.isspecial(name)

        else:
            typestr, special = self.typecodes.typestr(type)


        fieldnames = []
        if type.decls is not None:
            for decl in type.decls:
                fieldnames.append(decl.name)
                ts, _ = self.typecodes.typestr(decl.type)
                if '?' in ts:
                    #print "Skip %s: contains function pointers"%(name,)
                    return

        self.structs[name] = {
            'typestr': typestr,
            'fieldnames': fieldnames,
            'special': special,
        }

    def add_extern(self, name, type):
        typestr, _ = self.typecodes.typestr(type)
        self.externs[name] = {
            'typestr': typestr,
        }

    def parse_defines(self, fname):
        p = subprocess.Popen(
            ['clang', '-arch', self.arch, '-isysroot', self.sdk, '-E', '-Wp,-dD', fname],
            stdout=subprocess.PIPE)
        data = p.communicate()[0]
        xit = p.wait()
        if xit != 0:
            print "WARNING: Cannot extract #defines from file"
            return []

        curfile = None
        lines = data.splitlines()
        for ln_idx, ln in enumerate(lines):
            m = LINE_RE.match(ln)
            if m is not None:
                curfile = m.group(1)

            if curfile is None or self.framework_path not in curfile:
                # Ignore definitions in wrong file
                continue

            if self.only_headers:
                if os.path.basename(curfile) not in self.only_headers:
                    continue

            m = DEFINE_RE.match(ln)
            if m is not None:
                key = m.group(1)
                if key.startswith('__'):
                    # Ignore private definitions
                    continue


                value = m.group(2)
                if value.endswith('\\'):
                    # Complex macro, ignore
                    print "IGNORE", repr(key), repr(value)
                    continue

                value = value.strip()

                if value in ('static inline',):
                    continue

                if not value:
                    # Ignore empty macros
                    continue

                m = INT_RE.match(value)
                if m is not None:
                    self.enum_values[key] = int(m.group(1), 0)
                    continue

                m = FLOAT_RE.match(value)
                if m is not None:
                    self.literals[key] = float(m.group(1))
                    continue

                m = STR_RE.match(value)
                if m is not None:
                    self.literals[key] = m.group(1)
                    continue

                m = UNICODE_RE.match(value)
                if m is not None:
                    self.literals[key] = unicode(m.group(1))
                    continue

                m = UNICODE2_RE.match(value)
                if m is not None:
                    self.literals[key] = unicode(m.group(1))
                    continue

                if value in ('nil', 'NULL', 'Nil'):
                    self.literals[key] = None
                    continue

                m = ALIAS_RE.match(value)
                if m is not None:
                    if key.startswith('AVAILABLE_MAC_OS_X'):
                        # Ignore availability macros
                        continue

                    if key.endswith('_EXPORT') or key.endswith('_IMPORT'):
                        continue

                    value = m.group(1)
                    if value not in ('extern', 'static', 'inline', 'float',):
                        self.aliases[key] = m.group(1)
                    continue

                if value == '(INT_MAX-1)':
                    if self.arch in ('i386', 'ppc'):
                        self.enum_values[key] = (1 << 31)-1
                        continue

                    elif self.arch in ('x86_64', 'ppc64'):
                        self.enum_values[key] = (1 << 63)-1
                        continue


                if key in ('NS_DURING', 'NS_HANDLER', 'NS_ENDHANDLER'):
                    # Classic exception handling in Foundation
                    continue

                if '__attribute__' in value or '__inline__' in value:
                    # It's fairly common to define macros for attributes,
                    # like '#define AM_UNUSED __attribute__((unused))'
                    continue

                if value.startswith('CFUUIDGetConstantUUIDWithBytes('):
                    # Constant CFUUID definitions, used in a number of
                    # frameworks
                    self.called_definitions[key] = value.replace('NULL', 'None')
                    continue

                m = ERR_SUB_DEFINE_RE.match(value)
                if m is not None:
                    v = parse_int(m.group(1))
                    self.enum_values[key] = (v&0xfff)<<14
                    continue

                m = ERR_SYSTEM_DEFINE_RE.match(value)
                if m is not None:
                    v = parse_int(m.group(1))
                    self.enum_values[key] = (v&0x3f)<<26
                    continue

                m = NULL_VALUE.match(value)
                if m is not None:
                    # For #define's like this:  #define kDSpEveryContext ((DSpContextReference)NULL)
                    self.literals[key] = None
                    continue



                m = SC_SCHEMA_RE.match(value)
                if m is not None:
                    self.externs[key] = objc._C_ID
                    continue

                m = OR_EXPR_RE.match(value)
                if m is not None:
                    self.expressions[key] = value
                    continue

                m = CALL_VALUE.match(value)
                if m is not None:
                    self.expressions[key] = value
                    continue

                print "Warning: ignore #define %r %r"%(key, value)

            m = FUNC_DEFINE_RE.match(ln)
            if m is not None:
                proto = m.group(1)
                body = m.group(2).strip()
                if body == '\\':
                    body = body[ln_idx+1].strip()
                    if body.endswith('\\'):
                        print "Warning: ignore complex function #define %s"(proto,)
                        continue

                funcdef = "def %s: return %s"%(proto, body)
                try:
                    compile(funcdef, '-', 'exec')
                except SyntaxError:
                    pass

                else:
                    self.func_macros[proto.split('(')[0]] = funcdef

    
    def add_function(self, name, type, funcspec):
        if name.startswith('__'):
            return

        self.functions[name] = func = { 
            'retval': {'typestr': None },
            'args': [],
        }

        if 'Create' in name or 'Copy' in name:
            # FIXME: Net to check that the next character is not a lowercase alpha.
            func['retval']['already_cfretained'] = True


        if 'inline' in funcspec or '__inline__' in funcspec:
            func['inline'] = True

        func['retval']['typestr'] = self.typecodes.typestr(type.type)[0]
        if type.args is None:
            func['xxx-no-params'] = True
        for arg in (type.args.params if type.args is not None else []):
            if isinstance(arg, c_ast.EllipsisParam):
                func['variadic'] = True
                continue

            arginfo = {
                'name': arg.name,
                'typestr': self.typecodes.typestr(arg.type)[0],
            }

            tp = arg.type
            if isinstance(tp, c_ast.TypeDecl) and isinstance(tp.type, c_ast.IdentifierType):
                if tp.type.names[0] in self._func_protos:
                    arginfo['function'] = self._func_protos[tp.type.names[0]]

                else:
                    try:
                        tp = self.typedefs[tp.type.names[0]]
                    except KeyError:
                        pass

            if isinstance(tp, c_ast.BlockPtrDecl):
                arginfo['block'] = self.extract_block(tp)


            if isinstance(tp, c_ast.PtrDecl) and isinstance(tp.type, c_ast.FuncDecl):
                arginfo['function'] = self.extract_function(tp)

            if iscferrorptr(tp):
                # Function returns a CFError object by reference

                # This is an output argument:
                arginfo['type_modifier'] = objc._C_OUT

                # Where we can pass in 'nil' if we don't want the error object
                arginfo['null_accepted'] = True

                # User must CFRelease the error object
                arginfo['already_cfretained'] = True

            func['args'].append(arginfo)

        if name.endswith('GetTypeID'):
            tp = name[:-9] + 'Ref'
            if tp in self.cftypes:
                self.cftypes[tp]['gettypeid_func'] = name

        if func.get('variadic', False):
            for a in func['args']:
                if a['name'] == 'format' and a['typestr'] in ('^{__CFString=}', '@'):
                    a['printf_format'] = True

    def extract_block(self, blockptr):
        if not isinstance(blockptr.type, c_ast.FuncDecl):
            print "WARNING: Cannot extract block info"
            return {}

        func = blockptr.type

        result = {}
        result['retval'] = {
            'typestr': self.typecodes.typestr(func.type),
        }
        result['args'] = []
        if func.args is not None:
            for a in func.args.params:
                if isinstance(a, c_ast.EllipsisParam):
                    result['variadic'] = True
                    continue
                result['args'].append({
                    'typestr': self.typecodes.typestr(a.type),
                })
        return result

    def extract_function(self, functionptr):
        if not isinstance(functionptr.type, c_ast.FuncDecl):
            print "WARNING: Cannot extract function info"
            return {}

        func = functionptr.type

        result = {}
        result['retval'] = {
            'typestr': self.typecodes.typestr(func.type),
        }
        result['args'] = []
        if func.args is not None:
            for a in func.args.params:
                if isinstance(a, c_ast.EllipsisParam):
                    result['variadic'] = True
                    continue
                result['args'].append({
                    'typestr': self.typecodes.typestr(a.type),
                })
        return result

    def extract_methoddecl(self, decl):
        """
        Return interesting information from a MethodDecl
        """
        if decl.retval is None:
            tc = '@', False
        else:
            tc = self.typecodes.typestr(decl.retval.type)

        meth = {
            'selector': decl.selector,
            'class_method': decl.class_method,
            'retval': {
                'typestr': tc[0],
                'typestr_special': tc[1],
            },
            'args': [
            ],
        }
        for a in decl.args:
            if isinstance(a, c_ast.EllipsisParam):
                meth['variadic'] = True
                continue

            if a is None:
                tc = '@', False
            else:
                tc = self.typecodes.typestr(a.type)
            meth['args'].append({
                'typestr': tc[0],
                'typestr_special': tc[1],
            })

        return meth


    def add_protocol(self, node):
        self.formal_protocols[node.name] = protocol = {
            'implements': node.protocols,
            'methods': [],
            'properties': [],
        }
        cur_visibility='public'
        cur_required=True

        for decl in node.decls:
            if isinstance(decl, c_ast.Visibility):
                if decl.kind in ('@public', '@private', '@protected', '@package'):
                    cur_visibility = decl.kind[1:]
                elif decl.kind in ('@required', '@optional'):
                    cur_required = (decl.kind == '@required')
                else:
                    raise ValueError(node.kind)

            elif isinstance(decl, c_ast.MethodDecl):
                meth = self.extract_methoddecl(decl)
                meth['visibility'] = cur_visibility
                meth['required'] = cur_required
                protocol['methods'].append(meth)

            elif isinstance(decl, c_ast.Property):
                for item in decl.decl:
                    tc = self.typecodes.typestr(item.type)
                    protocol['properties'].append({
                        'name': item.name,
                        'typestr': tc[0],
                        'typestr_special': tc[1],
                    })

            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

    def add_informal_protocol(self, node):
        self.informal_protocols[node.categorie_name] = protocol = {
            'implements': node.protocols,
            'methods': [],
            'properties': [],
        }
        cur_visibility='public'

        for decl in node.decls:
            if isinstance(decl, c_ast.Visibility):
                if decl.kind in ('@public', '@private', '@protected', '@package'):
                    cur_visibility = decl.kind[1:]
                else:
                    raise ValueError(node.kind)

            elif isinstance(decl, c_ast.MethodDecl):
                meth = self.extract_methoddecl(decl)
                meth['visibility'] = cur_visibility
                protocol['methods'].append(meth)

            elif isinstance(decl, c_ast.Property):
                for item in decl.decl:
                    tc = self.typecodes.typestr(item.type)
                    protocol['properties'].append({
                        'name': item.name,
                        'typestr': tc[0],
                        'typestr_special': tc[1],
                    })

            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

    def add_interface(self, node):

        if node.name in self.classes:
            class_info = self.classes[node.name]
        else:
            class_info = self.classes[node.name] = {
                    'name': node.name,
                    'super': node.super,
                    'protocols': set(),
                    'methods': [],
                    'categories': [],
                    'properties': [],
            }
        class_info['protocols'].update(node.protocol)

        cur_visibility='public'

        for decl in node.decls or []:
            if isinstance(decl, c_ast.Visibility):
                if decl.kind in ('@public', '@private', '@protected', '@package'):
                    cur_visibility = decl.kind[1:]
                else:
                    raise ValueError(node.kind)

            elif isinstance(decl, c_ast.MethodDecl):
                meth = self.extract_methoddecl(decl)
                meth['visibility'] = cur_visibility
                class_info['methods'].append(meth)

            elif isinstance(decl, c_ast.Property):
                for item in decl.decl:
                    tc = self.typecodes.typestr(item.type)
                    class_info['properties'].append({
                        'name': item.name,
                        'typestr': tc[0],
                        'typestr_special': tc[1],
                        'attributes': decl.attributes,
                    })

            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

    def add_category(self, node):
        try:
            class_info = self.classes[node.name]
        except KeyError:
            class_info = self.classes[node.name] = {
                    'name': node.name,
                    'methods': [],
                    'protocols': set(),
                    'properties': [],
            }

        if node.protocols:
            class_info['protocols'].update(node.protocols)

        cur_visibility='public'

        for decl in node.decls:
            if isinstance(decl, c_ast.Visibility):
                if decl.kind in ('@public', '@private', '@protected', '@package'):
                    cur_visibility = decl.kind[1:]
                else:
                    raise ValueError(node.kind)

            elif isinstance(decl, c_ast.MethodDecl):
                meth = self.extract_methoddecl(decl)
                meth['visibility'] = cur_visibility
                class_info['methods'].append(meth)

            elif isinstance(decl, c_ast.Property):
                for item in decl.decl:
                    tc = self.typecodes.typestr(item.type)
                    class_info['properties'].append({
                        'name': item.name,
                        'typestr': tc[0],
                        'typestr_special': tc[1],
                        'attributes': decl.attributes,
                    })

            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass






def iscferrorptr(node):
    if not isinstance(node, c_ast.PtrDecl):
        return False

    if not isinstance(node.type, c_ast.TypeDecl):
        return False

    t = node.type.type
    if isinstance(t, c_ast.IdentifierType) and t.names[0] == 'CFErrorRef':
        return True

    return False


if __name__ == "__main__":
    p = FrameworkParser('InputMethodKit', start_header='InputMethodKit/InputMethodKit.h')
    p.parse()

    import pprint
    pprint.pprint(p.definitions())
