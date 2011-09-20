"""
A simple type code calculator
"""
from objective.cparser import c_ast
import objc
from ast_tools import constant_fold


class _Visitor (c_ast.NodeVisitor):
    def __init__(self, registry):
        super(_Visitor, self).__init__()
        self._registry = registry

    def visit_Typedef(self, node):
        self._registry.add_typedef(node.name, node.type)

class TypeCodes (object):
    def __init__(self):
        self._definitions = {}
        self._special = set()

        self.__add_predefined()

    def fill_from_ast(self, ast):
        v = _Visitor(self)
        v.visit(ast)

    def add_classdef(self, name):
        self._definitions[name] = objc._C_ID

    def add_typedef(self, name, node):
        if name in self._definitions:
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

            print "add", name, self._definitions[name], special
        

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

    def __getitem__(self, key):
        # Key is either a string or a ast node for a type
        if isinstance(key, (str, unicode)):
            return self._definitions[key]

        return self.__typestr_for_node(key)[0]


    def __typestr_for_node(self, node, name=None):
        special = False
        if isinstance(node, c_ast.TypeDecl):
            return self.__typestr_for_node(node.type)

        if isinstance(node, c_ast.Enum):
            return objc._C_INT, special

        if isinstance(node, c_ast.Struct):
            result = [ objc._C_STRUCT_B ]
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
            dim = constant_fold(node.dim)
            if not isinstance(dim, c_ast.Constant):
                print "Cannot process at", node.coord
                return None, None

            result.append(dim.value)

            t, s = self.__typestr_for_node(node.type)
            result.append(t)
            if s:
                special = True
            
            result.append(objc._C_ARY_E)
            return ''.join(result), special

        if isinstance(node, c_ast.PtrDecl):
            t, s = self.__typestr_for_node(node.type)
            return objc._C_PTR + t, s

        if isinstance(node, c_ast.FuncDecl):
            return objc._C_UNDEF, special


        if isinstance(node, c_ast.IdentifierType):
            key = ' '.join(node.names)
            return self._definitions[key], key in self._special

        raise ValueError(node)
        # XXX
        

    def __add_predefined(self):
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
        self.add_predefined('int signed', objc._C_INT)
        self.add_predefined('int unsigned', objc._C_UINT)
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
        self.add_predefined('signed long long', objc._C_LNG_LNG)
        self.add_predefined('unsigned long long', objc._C_ULNG_LNG)
        self.add_predefined('long long signed', objc._C_LNG_LNG)
        self.add_predefined('long long unsigned', objc._C_ULNG_LNG)
        self.add_predefined('bool', objc._C_BOOL)
        self.add_predefined('float', objc._C_FLT)
        self.add_predefined('double', objc._C_DBL)
        self.add_predefined('id', objc._C_ID)
        self.add_predefined('void', objc._C_VOID)

        # XXX: Not entirely correct:
        self.add_predefined('__builtin_va_list', objc._C_PTR + objc._C_VOID )

        # Some types that are typedefs of an integer type, but are
        # treated specially by PyObjC
        self.add_predefined('BOOL',      objc._C_NSBOOL, True)
        self.add_predefined('Boolean',   objc._C_NSBOOL, True)
        self.add_predefined('boolean_t', objc._C_NSBOOL, True)
        self.add_predefined('int8_t',    objc._C_CHAR_AS_INT, True)
        self.add_predefined('UniChar',   objc._C_UNICHAR, True)

