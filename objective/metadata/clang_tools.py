import clang.cindex

class NodeVisitor (object):
    def visit(self, node):
        try:
            method = 'visit_' + node.kind.name
        except ValueError:
            visitor = self.generic_visit

        else:
            visitor = getattr(self, method, self.generic_visit)

        visitor(node)

    def generic_visit(self, node):
        if hasattr(node, 'get_children'):
            for n in list(node.get_children()):
                self.visit(n)

class FilteredVisitor (NodeVisitor):
    def __init__(self, parser):
        self._parser = parser

    def visit(self, node):
        if not self._parser._select_node(node):
            self.generic_visit(node)
            return

        super(FilteredVisitor, self).visit(node)


def dump_type(node, indent=''):
    print(indent + node.kind.name, node.is_pod())
    print(indent + node.spelling)
    print(indent + "argument_types:", list(node.argument_types()))
    #dump_type(node.argument_types(), indent + '  ')
    #print(indent + "element_count:", node.element_count)
    #print(indent + "element_type:", node.element_type)
    #print(indent + "from_result:", node.from_result())
    print(indent + "get_array_element_type:", node.get_array_element_type())
    print(indent + "get_array_size:", node.get_array_size())
    print(indent + "data", node.data)
    print(indent + "get_canonical:", node.get_canonical())
    #print(dir(node))
    canonical = node.get_canonical()
    if canonical is not None and node.kind is clang.cindex.TypeKind.TYPEDEF:
        dump_type(node.get_canonical(), indent+'  ')
    print(indent + "get_declaration:", node.get_declaration())
    print(indent + "get_pointee:", node.get_pointee())
    print(indent + "get_result:", node.get_result())
    print(indent + "is_const_qualified:", node.is_const_qualified())
    #print(indent + "is_function_variadic:", node.is_function_variadic())
    print(indent + "is_pod:", node.is_pod())
    print(indent + "is_restrict_qualified:", node.is_restrict_qualified())
    print(indent + "kind:", node.kind)
    print(indent + "translation_unit:", node.translation_unit)
"""
    <clang.cindex.Type object at 0x10feb1f80>['__class__', '__ctypes_from_outparam__', '__delattr__', '__dict__', '__doc__', '__eq__', '__format__', '__getattribute__', '__hash__', '__init__', '__module__', '__ne__', '__new__', '__reduce__', '__reduce_ex__', '__repr__', '__setattr__', '__setstate__', '__sizeof__', '__str__', '__subclasshook__', '__weakref__', '_b_base_', '_b_needsfree_', '_fields_', '_kind_id', '_objects', '_tu', 'argument_types', 'data', 'element_count', 'element_type', 'from_result', 'get_array_element_type', 'get_array_size', 'get_canonical', 'get_declaration', 'get_pointee', 'get_result', 'is_const_qualified', 'is_function_variadic', 'is_pod', 'is_restrict_qualified', 'is_volatile_qualified', 'kind', 'translation_unit']
"""

def dump_node(node, indent=''):
    header = [type(node).__name__, node.kind.name]
    if node.spelling:
        header.append("spelling="+node.spelling)
    if node.displayname:
        header.append("displayname="+node.displayname)
    if node.objc_type_encoding and node.objc_type_encoding != '?':
        header.append("encoding=%r"%(node.objc_type_encoding,))
    header.append("is_attribute=%r"%(node.kind.is_attribute()))
    print(indent + ' '.join(header))
    if node.type:
        print(indent + '  type:')
        dump_type(node.type, indent+'    ')

    children = list(node.get_children())
    if children:
        print(indent + '  children:')
        for ch in children:
            dump_node(ch, indent+'    ')
