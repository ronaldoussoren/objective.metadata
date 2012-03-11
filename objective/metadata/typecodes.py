"""
A simple type code calculator

TODO:
- Calculation of typestr for struct typedefs doesn't work as well as I'd like
  (in the from_ast function), haven't looked into that yet
- TODO: Better handling of incomplete struct type, in particular this case:

    typedef struct Foo Foo;
    struct Foo {
       int a;
    }

  Used in the header file for CGAffineTransform

"""
from objective.cparser import c_ast
import objc
from ast_tools import constant_fold, parse_int
import collections


class _Visitor (c_ast.NodeVisitor):
    def __init__(self, registry):
        super(_Visitor, self).__init__()
        self._registry = registry
        self._seen_structs = {}
        self._incomplete_structs = collections.defaultdict(list)

    def finish(self):
        for struct_name, type_nodes in self._incomplete_structs.items():
            if not type_nodes: continue

            for node in type_nodes:
                self._registry.add_typedef(node.name, node.type)

    def visit_Interface(self, node):
        self.generic_visit(node)

        self._registry._classes.add(node.name)

    def visit_ForwardClass(self, node):
        self._registry._classes.add(node.name)

    def visit_Struct(self, node):
        if node.name is not None and node.decls is not None:
            if node.name not in self._seen_structs:
                self._seen_structs[node.name] = node

                if node.name in self._incomplete_structs:
                    for type_node in self._incomplete_structs[node.name]:
                        self._registry.add_typedef(type_node.name, node, force=True)
                    self._incomplete_structs[node.name] = []


    def visit_Typedef(self, node):
        type = node.type
        while isinstance(type, c_ast.TypeDecl):
            type = type.type
        if isinstance(type, c_ast.Struct) and type.decls is None:
            if type.name in self._seen_structs:
                self._registry.add_typedef(node.name, self._seen_structs[type.name])
                return
            else:
                self._incomplete_structs[type.name].append(node)

        self._registry.add_typedef(node.name, node.type)

    def visit_EnumeratorList(self, node):
        prev_name = None
        prev_value = None
        for item in node.children():

            value = item.value
            if value is not None:
                value = constant_fold(value, self._registry._enum_values)

            if value is None:
                if prev_value is not None:
                    value = prev_value + 1

                elif prev_name is not None:
                    value = c_ast.BinaryOp(
                        '+', prev_name, c_ast.Constant('int', 1))

                else:
                    value = 0

            elif isinstance(value, c_ast.Constant) and value.type == 'int':
                try:
                    value = parse_int(value.value)
                except ValueError:
                    value = None

            prev_name = item.name
            if isinstance(value, int):
                prev_value = value
            else:
                prev_value = None
                continue

            self._registry._enum_values[item.name] = value


class TypeCodes (object):
    def __init__(self, arch, typemap=None):
        self._definitions = {}
        self._special = set()

        self.__add_predefined(arch)
        self._enum_values = {}
        self._classes = set()

        if typemap is None:
            self._typemap = {}
        else:
            self._typemap = typemap

    def fill_from_ast(self, ast):
        v = _Visitor(self)
        v.visit(ast)
        v.finish()

    def _add_enum(self, node):
        pass

    def add_classdef(self, name):
        self._definitions[name] = objc._C_ID

    def add_typedef(self, name, node, force=False):
        if not force and (name in self._definitions):
            return

        if isinstance(node, (str, unicode)):
            self._definitions[name] = self._definitions[node]
            if node in self._special:
                self._special.add(name)

        else:
            typestr, special = self.__typestr_for_node(node, name)
            if typestr is None:
                return

            self._definitions[name] = typestr 
            if special:
                self._special.add(name)


    def add_predefined(self, name, typestr, isspecial=False):
        self._definitions[name] = typestr
        if isspecial:
            self._special.add(name)

    def isspecial(self, name):
        return name in self._special

    def __contains__(self, key):
        if isinstance(key, (str, unicode)):
            return key in self._definitions


        else:
            # It should always be possible to calculate a
            # typestr from a Node
            return True

    def typestr(self, key):
        return self.__typestr_for_node(key)

    def __getitem__(self, key):
        # Key is either a string or a ast node for a type
        if isinstance(key, (str, unicode)):
            return self._definitions[key]

        return self.__typestr_for_node(key)[0]


    def __typestr_for_node(self, node, name=None):
        value, special = self.__calc_typestr_for_node(node, name)
        if value in self._typemap:
            value = self._typemap[value]

        return value, special

    def __calc_typestr_for_node(self, node, name=None):
        special = False
        if isinstance(node, c_ast.TypeDecl):
            return self.__typestr_for_node(node.type, name)

        if isinstance(node, c_ast.Enum):
            return objc._C_INT, special

        if isinstance(node, c_ast.Struct):
            result = [ objc._C_STRUCT_B ]
            if node.name is None:
                if name is not None:
                    result.append('_' + name)
                    special=True
            else:
                result.append(node.name)


            result.append('=')

            if node.decls is not None:
                for d in node.decls:
                    t, s = self.__typestr_for_node(d.type)
                    if t is None:
                        return None, None
                    if s:
                        special = True

                    result.append(t)

            result.append(objc._C_STRUCT_E)
            return ''.join(result), special

        if isinstance(node, c_ast.Union):
            result = [ objc._C_UNION_B ]
            if node.name is None:
                if name is not None:
                    result.append(name)
                    special=True
            else:
                result.append(node.name)

            result.append('=')

            if node.decls is not None:
                for d in node.decls:
                    t, s = self.__typestr_for_node(d.type)
                    if s:
                        special = True

                    result.append(t)

            result.append(objc._C_UNION_E)
            return ''.join(result), special

        if isinstance(node, c_ast.ArrayDecl):
            result = []
            result.append(objc._C_ARY_B)
            dim = constant_fold(node.dim, self._enum_values)
            if dim is None:
                t, s = self.__typestr_for_node(node.type)
                return objc._C_PTR + t, s

            if not isinstance(dim, c_ast.Constant):
                print "WARNING: Cannot encode array type at", node.coord
                return None, None

            result.append(dim.value)

            t, s = self.__typestr_for_node(node.type)
            result.append(t)
            if s:
                special = True
            
            result.append(objc._C_ARY_E)
            return ''.join(result), special

        if isinstance(node, c_ast.PtrDecl):
            if isinstance(node.type.type, c_ast.IdentifierType):
                if node.type.type.names[0] in self._classes:
                    return objc._C_ID, special

            t, s = self.__typestr_for_node(node.type)
            return objc._C_PTR + t, s

        if isinstance(node, c_ast.FuncDecl):
            return objc._C_UNDEF, special


        if isinstance(node, c_ast.IdentifierType):
            if isinstance(node.names, str):
                key = node.names
            else:
                key = ' '.join(node.names)
            return self._definitions[key], key in self._special
        
        if isinstance(node, c_ast.BlockPtrDecl):
            return objc._C_ID + objc._C_UNDEF, special

        if isinstance(node, c_ast.Typename):
            return self.__typestr_for_node(node.type)

        raise ValueError(node)
        # XXX
        

    def __add_predefined(self, arch):
        self.add_predefined('_Bool', objc._C_BOOL)
        self.add_predefined('char', objc._C_CHR)
        self.add_predefined('signed char', objc._C_CHR)
        self.add_predefined('char signed', objc._C_CHR)
        self.add_predefined('unsigned char', objc._C_UCHR)
        self.add_predefined('char unsigned', objc._C_UCHR)
        self.add_predefined('signed short', objc._C_SHT)
        self.add_predefined('short signed', objc._C_SHT)
        self.add_predefined('short', objc._C_SHT)
        self.add_predefined('unsigned short', objc._C_USHT)
        self.add_predefined('short unsigned', objc._C_USHT)
        self.add_predefined('int', objc._C_INT)
        self.add_predefined('signed int', objc._C_INT)
        self.add_predefined('unsigned int', objc._C_UINT)
        self.add_predefined('unsigned', objc._C_UINT)
        self.add_predefined('int signed', objc._C_INT)
        self.add_predefined('int unsigned', objc._C_UINT)

        if arch in ('x86_64', 'ppc64'):
            self.add_predefined('long', objc._C_LNG_LNG)
            self.add_predefined('long int', objc._C_LNG_LNG)
            self.add_predefined('int long', objc._C_LNG_LNG)
            self.add_predefined('int signed long', objc._C_LNG_LNG)
            self.add_predefined('int unsigned long', objc._C_ULNG_LNG)
            self.add_predefined('signed long', objc._C_LNG_LNG)
            self.add_predefined('unsigned long', objc._C_ULNG_LNG)
            self.add_predefined('long signed', objc._C_LNG_LNG)
            self.add_predefined('long unsigned', objc._C_ULNG_LNG)

        else:
            self.add_predefined('long', objc._C_LNG)
            self.add_predefined('long int', objc._C_LNG)
            self.add_predefined('int long', objc._C_LNG)
            self.add_predefined('int signed long', objc._C_LNG)
            self.add_predefined('int unsigned long', objc._C_ULNG)
            self.add_predefined('signed long', objc._C_LNG)
            self.add_predefined('unsigned long', objc._C_ULNG)
            self.add_predefined('long signed', objc._C_LNG)
            self.add_predefined('long unsigned', objc._C_ULNG)

        self.add_predefined('long long', objc._C_LNG_LNG)
        self.add_predefined('int long long', objc._C_LNG_LNG)
        self.add_predefined('signed long long', objc._C_LNG_LNG)
        self.add_predefined('int signed long long', objc._C_LNG_LNG)
        self.add_predefined('unsigned long long', objc._C_ULNG_LNG)
        self.add_predefined('int unsigned long long', objc._C_ULNG_LNG)
        self.add_predefined('long long signed', objc._C_LNG_LNG)
        self.add_predefined('long long unsigned', objc._C_ULNG_LNG)
        self.add_predefined('bool', objc._C_BOOL)
        self.add_predefined('float', objc._C_FLT)
        self.add_predefined('double', objc._C_DBL)
        self.add_predefined('id', objc._C_ID)
        self.add_predefined('void', objc._C_VOID)
        self.add_predefined('SEL', objc._C_SEL)

        # XXX: Not entirely correct:
        self.add_predefined('__builtin_va_list', objc._C_PTR + objc._C_VOID )

        # Some types that are typedefs of an integer type, but are
        # treated specially by PyObjC
        self.add_predefined('BOOL',      objc._C_NSBOOL, True)
        self.add_predefined('Boolean',   objc._C_NSBOOL, True)
        self.add_predefined('boolean_t', objc._C_NSBOOL, True)
        self.add_predefined('int8_t',    objc._C_CHAR_AS_INT, True)
        self.add_predefined('UniChar',   objc._C_UNICHAR, True)

        # CFTypeRef is a 'void*' in the headers, but should be treated as
        # an object pointer by the bridge
        self.add_predefined('CFTypeRef',   objc._C_ID, True)

