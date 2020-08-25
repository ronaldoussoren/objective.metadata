"""
Utility module for parsing the header files in a framework and extracting
interesting definitions using Clang.

I realize that my "categories" on the libclang python classes are more Obj-C-ish
than "pythonic" but, hey, I'm an ObjC developer first...
"""


class AbstractClangVisitor(object):
    ###
    # A Visitor class to traverse libclang cursors
    ###

    def visitor_function_for_cursor(self, cursor):
        method_name = "visit_" + cursor.kind.name.lower()
        method = getattr(self, method_name, None)
        if method is None:
            method = self.descend
        return method

    def visit(self, cursor):
        visitor_function = self.visitor_function_for_cursor(cursor)
        return None if visitor_function is None else visitor_function(cursor)

    def descend(self, cursor):
        for c in cursor.get_children():
            self.visit(c)
