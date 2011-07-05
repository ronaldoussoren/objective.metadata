"""
Parsing framework headers and returning the usefull data
"""
from clang import cindex

import os

class HeaderParser (object):
    def __init__(self, framework):
        self._framework = framework
        self._framework_chk = '/%s.framework/'%(framework)
        self._index = cindex.Index.create()


        self._headers = set()
        self._definitions = {
            'enum': set(),
        }

    def parse(self):
        unit = self._index.parse("meta-extract.m",
                    unsaved_files=[
                        ("meta-extract.m", "#import <%s/%s.h>"%(self._framework, self._framework)),
                    ])
        self.process(unit)

    def process(self, unit):
        seen = set()
        for node in unit.cursor.get_children():
            if node.kind not in seen:
                seen.add(node.kind)
            if node.kind == cindex.CursorKind.PREPROCESSING_DIRECTIVE:
                print node

            if not self.is_framework_location(node.location):
                continue

            self._headers.add(os.path.basename(node.location.file.name))

            func = self._nodemap.get(node.kind)
            if func is None:
                continue


            func(self, node)

    def is_framework_location(self, location):
        if location.file == None:
            return False

        if self._framework_chk in location.file.name:
            return True

        return False

    _nodemap = {}

    def register(kind, _nodemap=_nodemap):
        def decorate(function):
            _nodemap[kind] = function
            return function
        return decorate

    @register(cindex.CursorKind.INCLUSION_DIRECTIVE)
    def _handle_include(self, node):
        print node



    @register(cindex.CursorKind.TYPEDEF_DECL)
    def _handle_typdef(self, node):
        print node.spelling, [(n.kind, n.spelling, n.displayName) for n in node.get_children()]

    @register(cindex.CursorKind.STRUCT_DECL)
    def _handle_struct(self, node):
        #print node.spelling, list(node.get_children())
        pass


    @register(cindex.CursorKind.UNION_DECL)
    def _handle_union(self, node):
        pass

    @register(cindex.CursorKind.FUNCTION_DECL)
    def _handle_function(self, node):
        #print node.spelling
        #print node.kind
        #print [n.kind for n in node.get_children()]
        pass

    @register(cindex.CursorKind.ENUM_DECL)
    def _handle_enum_decl(self, node):
        for label in node.get_children():
            assert label.kind is cindex.CursorKind.ENUM_CONSTANT_DECL, label.kind
            self._definitions['enum'].add(label.spelling)

    @register(cindex.CursorKind.OBJC_PROTOCOL_DECL)
    def _handle_protocol_decl(self, node):
        pass

    @register(cindex.CursorKind.OBJC_INTERFACE_DECL)
    def _handle_interface_decl(self, node):
        pass

    @register(cindex.CursorKind.OBJC_CATEGORY_DECL)
    def _handle_category_decl(self, node):
        pass


    del register


if __name__ == "__main__":
    p = HeaderParser("Foundation")
    p.parse()
