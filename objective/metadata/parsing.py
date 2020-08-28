"""
Utility module for parsing the header files in a framework and extracting
interesting definitions.
"""
import os
import platform
import re
import sys
import typing

import objc

from .clang import (
    Config,
    Cursor,
    CursorKind,
    Index,
    LinkageKind,
    NullabilityKind,
    ObjcDeclQualifier,
    TranslationUnit,
    TranslationUnitLoadError,
    Type,
    TypeKind,
)

Config.set_library_path(
    "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/"  # noqa: B950
)

from objective.metadata.clanghelpers import (  # isort:skip  # noqa: E402
    AbstractClangVisitor,
)


#
# NOTE: Use TypedDict for type annoations for now, my current plan is to
#       change this into dataclasses with explicit serialization later on.
#

AvailabilityInfo = typing.TypedDict(
    "AvailabilityInfo",
    {
        "unavailable": bool,
        "suggestion": str,
        "introduced": int,
        "deprecated": int,
        "deprecated_message": str,
    },
    total=False,
)


EnumOptions = typing.TypedDict(
    "EnumOptions",
    {
        "value": typing.Union[int, float],
        "enum_type": str,
        "availability": typing.Optional[AvailabilityInfo],
    },
)
EnumTypeOptions = typing.TypedDict(
    "EnumTypeOptions",
    {"typestr": bytes, "availability": typing.Optional[AvailabilityInfo]},
)
StructOptions = typing.TypedDict(
    "StructOptions",
    {"typestr": bytes, "fieldnames": typing.List[str], "typestr_special": bool},
)
ExternOptions = typing.TypedDict(
    "ExternOptions",
    {
        "typestr": bytes,
        "availability": typing.Optional[AvailabilityInfo],
        "type_name": str,
    },
    total=False,
)
ArgInfo = typing.TypedDict(
    "ArgInfo",
    {
        "typestr": bytes,
        "typestr_special": bool,
        "null_accepted": bool,
        "type_modifier": bytes,
        "type_name": str,
        "already_retained": bool,
        "already_cfretained": bool,
        "name": str,
        "callable": "typing.Optional[CallbackInfo]",
        "function": dict,  # These two should be replaced by 'callable'
        "block": dict,
    },
    total=False,
)
MethodInfo = typing.TypedDict(
    "MethodInfo",
    {
        "selector": str,
        "class_method": bool,
        "args": typing.List[ArgInfo],
        "retval": ArgInfo,
        "visibility": str,
        "required": bool,
        "variadic": bool,
        "printf_format": int,
        "null_terminated": bool,
        "category": typing.Optional[str],
        "availability": typing.Optional[AvailabilityInfo],
    },
    total=False,
)
FunctionInfo = typing.TypedDict(
    "FunctionInfo",
    {
        "args": typing.List[ArgInfo],
        "retval": ArgInfo,
        "variadic": bool,
        "printf_format": int,
        "inline": bool,
        "null_terminated": bool,
        "availability": typing.Optional[AvailabilityInfo],
    },
    total=False,
)


#
# See explaination for CallbackInfo below
#
# the "2" types are needed because MyPy doesn't
# support cyclic types. With some luck this should
# be good enough for us (at the cost of some code
# duplication)
CallbackArgInfo2 = typing.TypedDict(
    "CallbackArgInfo2",
    {
        "typestr": bytes,
        "typestr_special": bool,
        "null_accepted": bool,
        "type_modifier": bytes,
        "type_name": str,
        "already_retained": bool,
        "already_cfretained": bool,
        "name": str,
    },
    total=False,
)
CallbackInfo2 = typing.TypedDict(
    "CallbackInfo2",
    {
        "args": typing.List[CallbackArgInfo2],
        "retval": CallbackArgInfo2,
        "variadic": bool,
        "printf_format": int,
        "null_terminated": bool,
    },
    total=False,
)

# CallbackArgInfo is needed to break a cycle in the
# type graph, which mypy does not yet support.
#
# This needs to work to support blocks with blocks as one
# of their arguments.
CallbackArgInfo = typing.TypedDict(
    "CallbackArgInfo",
    {
        "typestr": bytes,
        "typestr_special": bool,
        "null_accepted": bool,
        "type_modifier": bytes,
        "type_name": str,
        "already_retained": bool,
        "already_cfretained": bool,
        "callable": typing.Optional[CallbackInfo2],
        "name": str,
    },
    total=False,
)

# Information about function/block signature for arguments
# and returns values.
CallbackInfo = typing.TypedDict(
    "CallbackInfo",
    {
        "args": typing.List[CallbackArgInfo],
        "retval": CallbackArgInfo,
        "variadic": bool,
        "printf_format": int,
        "null_terminated": bool,
    },
    total=False,
)

PropInfo = typing.TypedDict(
    "PropInfo",
    {
        "name": str,
        "typestr": bytes,
        "typestr_special": bool,
        "getter": typing.Optional[str],
        "setter": typing.Optional[str],
        "attributes": typing.Set[str],
        "availability": typing.Optional[AvailabilityInfo],
        "category": typing.Optional[str],
    },
)

ProtocolInfo = typing.TypedDict(
    "ProtocolInfo",
    {
        "implements": typing.List[str],
        "methods": typing.List[MethodInfo],
        "properties": typing.List[PropInfo],
        "availability": typing.Optional[AvailabilityInfo],
    },
)

ClassInfo = typing.TypedDict(
    "ClassInfo",
    {
        "super": typing.Optional[str],
        "protocols": typing.Set[str],
        "methods": typing.List[MethodInfo],
        "categories": typing.List[str],
        "properties": typing.List[PropInfo],
        "availability": typing.Optional[AvailabilityInfo],
    },
)

CFTypeInfo = typing.TypedDict(
    "CFTypeInfo",
    {"typestr": bytes, "gettypeid_func": str, "tollfree": str},
    total=False,
)

LiteralInfo = typing.TypedDict(
    "LiteralInfo",
    {"value": typing.Union[None, int, float, str], "unicode": bool},
    total=False,
)

AliasInfo = typing.TypedDict(
    "AliasInfo",
    {
        "alias": str,
        "enum_type": typing.Optional[str],
        "availability": typing.Optional[AvailabilityInfo],
    },
    total=False,
)

ExpressionInfo = typing.TypedDict(
    "ExpressionInfo",
    {"expression": str, "availability": typing.Optional[AvailabilityInfo]},
)

FunctionMacroInfo = typing.TypedDict(
    "FunctionMacroInfo",
    {"definition": str, "availability": typing.Optional[AvailabilityInfo]},
)


class SelfReferentialTypeError(Exception):
    pass


class FrameworkParser(object):
    """
    Parser for framework headers.

    This class uses libclang to do the actual work and stores
    all interesting information found in the headers.
    """

    def __init__(
        self,
        framework: str,
        arch: str = "x86_64",
        sdk: str = "/",
        start_header: typing.Optional[str] = None,
        preheaders: typing.Sequence[str] = (),
        extraheaders: typing.Sequence[str] = (),
        link_framework: typing.Optional[str] = None,
        only_headers: typing.Optional[typing.Sequence[str]] = None,
        min_deploy: typing.Optional[str] = None,
        verbose: bool = False,
    ):
        self.verbose = verbose
        self.framework = framework
        self.link_framework = (
            link_framework if link_framework is not None else framework
        )
        self.framework_path = "/%s.framework/" % (framework,)

        if start_header is not None:
            self.start_header = start_header
        else:
            self.start_header = "%s/%s.h" % (framework, framework)

        self.only_headers = only_headers

        self.preheaders = preheaders
        self.extraheaders = extraheaders
        self.additional_headers: typing.List[str] = []
        self.arch = arch
        self.sdk = sdk
        self.min_deploy = min_deploy

        self.headers: typing.Set[str] = set()

        self.enum_type: typing.Dict[str, EnumTypeOptions] = {}
        self.enum_values: typing.Dict[str, EnumOptions] = {}
        self.structs: typing.Dict[str, StructOptions] = {}
        self.externs: typing.Dict[str, ExternOptions] = {}
        self.literals: typing.Dict[str, LiteralInfo] = {}
        self.aliases: typing.Dict[str, AliasInfo] = {}
        self.expressions: typing.Dict[str, ExpressionInfo] = {}
        self.functions: typing.Dict[str, FunctionInfo] = {}  # incomplete type
        self.cftypes: typing.Dict[str, CFTypeInfo] = {}
        self.func_macros: typing.Dict[str, FunctionMacroInfo] = {}
        self.typedefs: typing.Dict = {}  # incomplete type
        self.formal_protocols: typing.Dict[str, ProtocolInfo] = {}
        self.informal_protocols: typing.Dict[str, ProtocolInfo] = {}
        self.classes: typing.Dict[str, ClassInfo] = {}
        self.macro_names: typing.Set[str] = set()

    def parse(self) -> None:

        index = Index.create()

        translation_unit = None

        if self.verbose:
            print("Beginning compilation with libclang...")

        options = (
            TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
            | TranslationUnit.PARSE_SKIP_FUNCTION_BODIES
            | TranslationUnit.INCLUDE_ATTRIBUTED_TYPES
            | TranslationUnit.VISIT_IMPLICIT_ATTRIBUTES
        )

        fake_path = "main.m"
        code_string = self.__includes_string()

        unsaved_files = [(fake_path, code_string)]
        args = ["-arch", self.arch, "-isysroot", self.sdk]

        # Handle min OS version deployment setting
        if self.min_deploy:
            # Replaces -DMAC_OS_X_VERSION_MIN_REQUIRED=1040 etc
            args.append("-mmacosx-version-min=" + str(self.min_deploy))

        # Parse
        try:
            translation_unit = index.parse(
                fake_path, args, unsaved_files, options=options
            )
        except TranslationUnitLoadError:
            sys.stderr.write("ERROR: libclang compilation failed")
            exit(1)

        # Not sure this is possible, but...
        if not translation_unit:
            sys.stderr.write("ERROR: libclang compilation failed")
            exit(1)

        if self.verbose:
            print("libclang compilation complete.")
            print("Beginning scan...")

        definitions_visitor = DefinitionVisitor(self)
        definitions_visitor.visit(translation_unit.cursor)

        if self.verbose:
            print("Scan complete.")

    def definitions(self) -> dict:  # incomplete type info
        """
        Returns a dictionary with information about what was parsed and
        the definitions.
        """
        return {
            "framework": self.framework,
            "arch": self.arch,
            "sdk": self.sdk,
            "release": platform.mac_ver()[0],
            "headers": sorted(self.headers),
            "definitions": {
                "enum": self.enum_values,
                "enum_type": self.enum_type,
                "structs": self.structs,
                "externs": self.externs,
                "literals": self.literals,
                "aliases": self.aliases,
                "expressions": self.expressions,
                "functions": self.functions,
                "cftypes": self.cftypes,
                "func_macros": self.func_macros,
                "formal_protocols": self.formal_protocols,
                "informal_protocols": self.informal_protocols,
                "classes": self.classes,
            },
        }

    @property
    def exceptions(self) -> dict:  # incomplete type info
        functions = {}
        for nm, definition in self.functions.items():
            info: dict = {}  # incomplete type information

            # My Read: Get rid of all function specs that take ptr arguments?
            for idx, a in enumerate(definition["args"]):
                a = a.copy()
                if a["typestr"].startswith(b"^"):
                    # a = dict(a)
                    del a["typestr"]
                    del a["name"]
                    try:
                        info["args"][idx] = a
                    except KeyError:
                        info["args"] = {idx: a}

            # My Read: Get rid of all typestrs of pointer return types
            if definition["retval"]["typestr"].startswith(b"^"):
                a = definition["retval"].copy()
                del a["typestr"]
                info["retval"] = a

            # My Read: Copy over the variadicness
            if definition.get("variadic", False):
                info["variadic"] = True

            # My Read: Add it
            if info:
                functions[nm] = info

        prots: dict = {}  # incomplete type information
        for section in ("formal_protocols", "informal_protocols"):
            prots[section] = {}
            for prot, protdef in getattr(self, section).items():
                for meth in protdef["methods"]:
                    info = {}
                    # My Read: remove metadata for protocol methods that
                    # take pointer args
                    for idx, a in enumerate(meth["args"]):
                        if a["typestr"].startswith(b"^"):
                            a = a.copy()
                            del a["typestr"]
                            del a["typestr_special"]
                            try:
                                info["args"][idx] = a
                            except KeyError:
                                info["args"] = {idx: a}

                    # My Read: remove metadata for protocol methods that return pointers
                    if meth["retval"]["typestr"].startswith(b"^"):
                        a = meth["retval"].copy()
                        del a["typestr"]
                        del a["typestr_special"]
                        info["retval"] = a

                    # My Read: copy over variadic flag
                    if meth.get("variadic", False):
                        info["variadic"] = True

                    # My Read: copy over other stuff
                    if info:
                        if prot not in prots[section]:
                            prots[section][prot] = {}
                        if "methods" not in prots[section][prot]:
                            prots[section][prot]["methods"] = []

                        info["selector"] = meth["selector"]
                        info["class_method"] = meth["class_method"]
                        prots[section][prot]["methods"].append(info)

                # My Read: remove metadata for propeties of pointer types
                for prop in protdef["properties"]:
                    if prop["typestr"].startswith(b"^"):
                        # My Read: for informal prots only, add a dictionary
                        if prot not in self.formal_protocols:
                            prots[section][prot] = {}
                        if "properties" not in prots[section][prot]:
                            prots[section][prot]["properties"] = []

                        prop = dict(prop)
                        del prop["typestr"]
                        del prop["typestr_special"]
                        prots[section][prot]["properties"].append(prop)

        classes: dict = {}  # incomplete type information
        for clsname, clsdef in self.classes.items():
            for method in clsdef["methods"]:
                info = {}
                for idx, a in enumerate(method["args"]):
                    if a["typestr"].startswith(b"^"):
                        a = a.copy()
                        del a["typestr"]
                        del a["typestr_special"]
                        try:
                            info["args"][idx] = a
                        except KeyError:
                            info["args"] = {idx: a}

                if method["retval"]["typestr"].startswith(b"^"):
                    a = method["retval"].copy()
                    del a["typestr"]
                    del a["typestr_special"]
                    info["retval"] = a

                if method.get("variadic", False):
                    info["variadic"] = True

                if info:
                    if clsname not in classes:
                        classes[clsname] = {}
                    if "methods" not in classes[clsname]:
                        classes[clsname]["methods"] = []

                    info["selector"] = method["selector"]
                    info["class_method"] = method["class_method"]
                    classes[clsname]["methods"].append(info)

            for prop in clsdef["properties"]:
                if prop["typestr"].startswith(b"^"):
                    if clsname not in classes:
                        classes[clsname] = {}
                    if "properties" not in classes[clsname]:
                        classes[clsname]["properties"] = []

                    prop = dict(prop)
                    del prop["typestr"]
                    del prop["typestr_special"]
                    classes[clsname]["properties"].append(prop)

        return {
            "definitions": {
                "functions": functions,
                "formal_protocols": prots["formal_protocols"],
                "informal_protocols": prots["informal_protocols"],
                "classes": classes,
            }
        }

    def should_process_cursor(self, cursor: Cursor) -> bool:
        """
        Return True iff ``node`` is an AST node that's loaded from a
        header for the current framework.
        """
        # always descend from the top...
        if cursor.kind == CursorKind.TRANSLATION_UNIT:
            return True

        framework_dir = self.framework_path
        while cursor and cursor.kind != CursorKind.TRANSLATION_UNIT:
            node_file = cursor.location.file
            if node_file and framework_dir in node_file.name:
                # make a note of it in our headers list, unless it's
                # the umbrella headers
                base_name = os.path.basename(node_file.name)
                if base_name != os.path.basename(self.start_header):
                    self.headers.add(base_name)
                # descend
                return True
            else:
                # check the next node up
                cursor = cursor.lexical_parent

        return False

    def add_alias(self, alias: str, value: str) -> None:
        assert isinstance(alias, str)
        assert isinstance(value, str)
        # Not adding availability is suboptimal...
        self.aliases[alias] = {"alias": value, "availability": None}

    def add_typedef(self, name: str, typedef: str) -> None:
        assert isinstance(name, str)
        assert isinstance(typedef, str)
        self.typedefs[name] = typedef

    def add_enumeration(self, node: Cursor) -> None:
        # Need to do something with availability information
        if node.spelling is None or node.spelling == "":
            name = "<anon>"
        else:
            name = node.spelling

        value_count = 0

        if node.kind == CursorKind.ENUM_CONSTANT_DECL:
            self.literals[name] = {"value": node.enum_value}
            value_count = 1

        elif node.kind == CursorKind.ENUM_DECL:
            typestr = self.__get_typestr(node)[0]
            assert typestr is not None
            self.enum_type[node.spelling] = {
                "typestr": typestr,
                "availability": self._get_availability(node),
            }
            for val in filter(
                lambda x: x.kind == CursorKind.ENUM_CONSTANT_DECL,
                node.get_children() or [],
            ):
                # Add the enum to the name -> value mapping
                val_name = val.spelling
                self.enum_values[val_name] = {
                    "value": val.enum_value,
                    "enum_type": node.spelling,
                    "availability": self._get_availability(val),
                }

                # Check to see if there's also an alias inherent in this declaration
                children = list(
                    filter(lambda x: x.kind.is_expression(), val.get_children())
                )
                if len(children) == 1:
                    referenced_decl = children[0].referenced_distinct
                    if (
                        referenced_decl
                        and referenced_decl.kind == CursorKind.ENUM_CONSTANT_DECL
                    ):
                        other_name = referenced_decl.spelling
                        if len(other_name or ""):
                            alias: AliasInfo = {
                                "alias": other_name,
                                "enum_type": node.spelling,
                                "availability": self._get_availability(val),
                            }

                            self.aliases[val_name] = alias
                elif len(children) > 1:
                    debug_break()

                value_count += 1

        if self.verbose:
            print("Added enumeration: %s with %d values" % (name, value_count))

    def add_cftype(
        self,
        name: str,
        cftype: Cursor,
        override_typestring: typing.Optional[bytes] = None,
    ) -> None:
        typestr = (
            override_typestring
            if override_typestring is not None
            else self.__get_typestr(cftype)[0]
        )
        assert typestr is not None
        self.cftypes[name] = {"typestr": typestr}
        if self.verbose:
            print("Added CFType: " + name)

    def add_struct(self, name: str, struct_type: Cursor) -> None:
        assert "union" not in name, name
        typestr, special = self.__get_typestr(struct_type, exogenous_name=name)
        assert typestr is not None

        fieldnames: typing.List[str] = []

        walker = struct_type
        while walker and walker.kind == TypeKind.TYPEDEF:
            next_type = walker.next_typedefed_type
            if next_type:
                walker = next_type
            else:
                break

        type_decl = walker.declaration
        if type_decl is not None:
            for field_decl in type_decl.get_struct_field_decls() or ():
                fieldnames.append(field_decl.spelling)
                ts, _ = self.__get_typestr(field_decl.type)
                assert ts

                if b"?" in ts:
                    # print("Skip %s: contains function pointers"%(name,))
                    return

        self.structs[name] = {
            "typestr": typestr,
            "fieldnames": fieldnames,
            "typestr_special": special,
        }

        if self.verbose:
            print("Added struct: ", name)

    def add_static_const(self, name: str, node: Cursor) -> None:
        node_type = node.type
        typestr, _ = self.__get_typestr(node_type)

        if typestr == b"?":
            clang_typestr = node.objc_type_encoding
            if len(str(clang_typestr)) > 0:
                typestr = clang_typestr

        for n in node.get_children():  # noqa: B007
            pass

        else:
            for m in n.get_children():
                # This needs to be fixed: When 'static const float X = FLT_MAX' has
                # an empty token_string.
                if m.kind == CursorKind.INTEGER_LITERAL:
                    if m.token_string == "":
                        print(f"Ignore {name!r}: empty token string")
                    else:
                        self.literals[name] = {"value": int(m.token_string)}

                elif m.kind == CursorKind.FLOATING_LITERAL:
                    if m.token_string == "":
                        print(f"Ignore {name!r}: empty token string")
                    else:
                        self.literals[name] = {"value": float(m.token_string)}

    def add_extern(self, name: str, node: Cursor) -> None:
        node_type = node.type
        typestr, special = self.__get_typestr(node_type)
        assert typestr is not None

        if not special and b"?" == typestr:
            clang_typestr = node.objc_type_encoding
            if len(str(clang_typestr)) > 0:
                typestr = clang_typestr

        extern: ExternOptions
        self.externs[name] = extern = {"typestr": typestr}
        type_name = self.get_typename(node_type)
        if type_name is not None:
            extern["type_name"] = type_name
        extern["availability"] = self._get_availability(node)
        if self.verbose:
            print(f"Added extern: {name} typestr {typestr.decode()!r}")

    def add_function(self, node: Cursor) -> None:
        name = node.spelling
        if name.startswith("__"):
            return

        funcspec = node.get_function_specifiers()

        func: FunctionInfo
        self.functions[node.spelling] = func = {"retval": {}, "args": []}
        func["availability"] = self._get_availability(node)

        # Does it follow the CF returns-retained pattern?
        if "Create" in name:
            parts = name.split("Create", 1)
            second_part = parts[-1]
            first_letter = None if len(second_part) == 0 else second_part[0]
            if not first_letter or first_letter.isupper():
                func["retval"]["already_cfretained"] = True

        elif "Copy" in name:
            parts = name.split("Copy", 1)
            second_part = parts[-1]
            first_letter = None if len(second_part) == 0 else second_part[0]
            if not first_letter or first_letter.isupper():
                func["retval"]["already_cfretained"] = True

        # is function inline?
        if "inline" in funcspec or "__inline__" in funcspec:
            func["inline"] = True

        # get return type
        return_type = node.result_type
        return_type_typestr = self.__get_typestr(return_type)[0]
        assert return_type_typestr is not None
        func["retval"]["typestr"] = return_type_typestr

        # get arguments
        arg_gen = node.get_arguments()
        args = [] if arg_gen is None else list(arg_gen)

        if node.type.is_function_variadic():
            func["variadic"] = True

        for arg in args:
            arg_type = arg.type
            typestr = self.__get_typestr(arg_type)[0]
            assert typestr is not None

            arginfo: ArgInfo = {"name": arg.spelling, "typestr": typestr}

            if arg_type.kind == TypeKind.BLOCKPOINTER:
                arginfo["callable"] = self.__extract_block(arg.type)

            if arg_type.looks_like_function:
                arginfo["callable"] = self.__extract_function(arg_type)

            # get qualifiers
            qualifiers = arg.objc_decl_qualifiers or ObjcDeclQualifier(
                ObjcDeclQualifier.Flag_None
            )

            # update for special cases
            if FrameworkParser.__node_is_cferror_ptr(arg_type):
                # Function returns a CFError object by reference
                qualifiers.is_out = True

                # Where we can pass in 'NULL' if we don't want the error object
                arginfo["null_accepted"] = True

                # User must CFRelease the error object
                arginfo["already_cfretained"] = True

            # write back qualifiers
            encode_str = qualifiers.to_encode_string()
            if encode_str != "":
                arginfo["type_modifier"] = encode_str

            # add the arginfo to the args array
            func["args"].append(arginfo)

        # special handling for CF GetTypeID functions
        if name.endswith("GetTypeID"):
            assert return_type.spelling == "CFTypeID"
            arg_type = name[:-9] + "Ref"
            if arg_type in self.cftypes:
                self.cftypes[arg_type]["gettypeid_func"] = name

        # Ideally libclang would give us full parsing of __attributes__
        # but it doesn't. We do the best we can, nd what libclang gives us
        # is arguably better than is_variadic and arg_name == "format"
        index_of_printf_format_arg = FrameworkParser.__index_of_printf_format_arg(node)
        if index_of_printf_format_arg is not None:
            if index_of_printf_format_arg < len(func["args"]):
                arg = func["args"][index_of_printf_format_arg]
                arg["printf_format"] = True
            else:
                # basically, libclang's C API doesnt expose the values
                # we need to be sure about this in all cases
                for arg in func["args"]:
                    if arg["name"] == "format":
                        arg["printf_format"] = True
        else:
            # Let's check the old way just in case. There *are* functions
            # in Apple's frameworks that don't have
            # the right __attribute__, believe it or not.
            if func.get("variadic"):
                for arg in func["args"]:
                    if arg["name"] == "format":
                        diagnostic_string = (
                            "Looks like we might have a printf function, but we didn't"
                            + "find the attribute; take a look: "
                            + node.get_raw_contents()
                            + str(node.location)
                        )
                        print(diagnostic_string)

        # Log message
        if self.verbose:
            string = (
                "Added function: "
                + name
                + " return typestr: "
                + func["retval"]["typestr"]
                + " args: "
            )
            for idx, arg in enumerate(func["args"]):
                string = (
                    string
                    + " "
                    + str(idx)
                    + ": "
                    + (arg["name"] or "")
                    + ", "
                    + (arg["typestr"] or "")
                )
            print(string)

    def add_protocol(self, node: Cursor) -> None:
        protocol: ProtocolInfo

        self.formal_protocols[node.spelling] = protocol = {
            "implements": [
                x.referenced.spelling for x in node.get_adopted_protocol_nodes()
            ],
            "methods": [],
            "properties": [],
            "availability": self._get_availability(node),
        }

        for decl in node.get_children() or []:
            if (
                decl.kind == CursorKind.OBJC_INSTANCE_METHOD_DECL
                or decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL
            ):
                meth = self.__extract_methoddecl(decl)
                meth["required"] = not decl.is_optional_for_protocol
                protocol["methods"].append(meth)

            elif decl.kind == CursorKind.OBJC_PROPERTY_DECL:
                typestr, special = self.__get_typestr(decl.type)
                assert typestr is not None

                prop_attr = decl.get_property_attributes()
                getter = setter = None
                attributes = set()
                for item in prop_attr or ():
                    if isinstance(item, str):
                        attributes.add(item)
                    elif item[0] == "getter":
                        getter = item[1]
                    elif item[0] == "setter":
                        setter = item[1]
                    else:
                        raise RuntimeError(f"Unexpected Attribute {attributes}")
                this_property: PropInfo = {
                    "name": decl.spelling,
                    "typestr": typestr,
                    "typestr_special": special,
                    "getter": getter,
                    "setter": setter,
                    "attributes": attributes,
                    "availability": self._get_availability(node),
                    "category": None,
                }
                protocol["properties"].append(this_property)
            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

        if self.verbose:
            print(f"Added protocol: {node.spelling}")

    def add_informal_protocol(self, node: Cursor) -> None:
        protocol: ProtocolInfo

        self.informal_protocols[node.spelling] = protocol = {
            "implements": [
                x.referenced.spelling for x in node.get_adopted_protocol_nodes()
            ],
            "methods": [],
            "properties": [],
            "availability": self._get_availability(node),
        }

        for decl in node.get_children() or []:
            if (
                decl.kind == CursorKind.OBJC_INSTANCE_METHOD_DECL
                or decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL
            ):
                meth = self.__extract_methoddecl(decl)
                protocol["methods"].append(meth)

            elif decl.kind == CursorKind.OBJC_PROPERTY_DECL:
                typestr, special = self.__get_typestr(decl.type)
                assert typestr is not None
                prop_attr = decl.get_property_attributes()
                getter = setter = None
                attributes = set()
                for item in prop_attr or ():
                    if isinstance(item, str):
                        attributes.add(item)
                    elif item[0] == "getter":
                        getter = item[1]
                    elif item[0] == "setter":
                        setter = item[1]
                    else:
                        raise RuntimeError(f"Unexpected Attribute {attributes}")
                this_property: PropInfo = {
                    "name": decl.spelling,
                    "typestr": typestr,
                    "typestr_special": special,
                    "getter": getter,
                    "setter": setter,
                    "attributes": attributes,
                    "availability": self._get_availability(node),
                    "category": None,
                }
                protocol["properties"].append(this_property)
            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

        if self.verbose:
            print("Added informal protocol: " + node.spelling)

    def _get_availability(self, node: Cursor) -> typing.Optional[AvailabilityInfo]:
        def encode_version(version):
            if version.major == -1:
                raise ValueError("Cannot encode version {version}")
            elif version.minor == -1:
                # "TO BE DEPRECATED"
                return version.major * 10000

            if version.minor < 9:
                return version.major * 100 + version.minor

            else:
                return (
                    version.major * 10000
                    + version.minor * 100
                    + (version.subminor if version.subminor != -1 else 0)
                )

        try:
            availability = node.platform_availability
        except AttributeError:
            return None

        meta: AvailabilityInfo = {}

        if availability["always_unavailable"]:
            suggestion = availability["unavailable_message"]
            meta["unavailable"] = True
            meta["suggestion"] = suggestion if suggestion else "not available"

        if availability["always_deprecated"]:
            # Deprecated without version number, assume this is deprecated starting
            # at the first darwin-based macOS (10.0)
            meta["deprecated"] = 10_00

        try:
            platform = availability["platform"]["macos"]

        except KeyError:
            pass

        else:
            if platform["introduced"] is not None:
                meta["introduced"] = encode_version(platform["introduced"])
            if platform["deprecated"] is not None:
                meta["deprecated"] = encode_version(platform["deprecated"])
                if platform["message"]:
                    meta["deprecated_message"] = platform["message"]
            if platform["unavailable"]:
                meta["unavailable"] = True
                meta["suggestion"] = (
                    platform["message"] if platform["message"] else "not available"
                )

        return meta if meta else None

    def add_class(self, node: Cursor):
        class_info: ClassInfo

        if node.spelling in self.classes:
            class_info = self.classes[node.spelling]
        else:
            # get superclass
            superclass_name = None
            for child in filter(
                lambda x: x.kind == CursorKind.OBJC_SUPER_CLASS_REF, node.get_children()
            ):
                superclass = child.referenced
                superclass_name = superclass.spelling

            self.classes[node.spelling] = class_info = {
                "super": superclass_name,
                "protocols": set(),
                "methods": [],
                "categories": [],
                "properties": [],
                "availability": self._get_availability(node),
            }

        for proto_ref in node.get_adopted_protocol_nodes():
            proto_str = proto_ref.referenced.spelling
            class_info["protocols"].add(proto_str)

        # This used to track visibility for methods, which is not a thing
        # (only for ivars) and made no sense

        for decl in node.get_children() or []:
            if (
                decl.kind == CursorKind.OBJC_INSTANCE_METHOD_DECL
                or decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL
            ):
                meth = self.__extract_methoddecl(decl)
                class_info["methods"].append(meth)

            elif decl.kind == CursorKind.OBJC_PROPERTY_DECL:
                typestr, special = self.__get_typestr(decl.type)
                assert typestr is not None

                prop_attr = decl.get_property_attributes()
                getter = setter = None
                attributes = set()
                for item in prop_attr or ():
                    if isinstance(item, str):
                        attributes.add(item)
                    elif item[0] == "getter":
                        getter = item[1]
                    elif item[0] == "setter":
                        setter = item[1]
                    else:
                        raise RuntimeError(f"Unexpected Attribute {attributes}")

                class_info["properties"].append(
                    {
                        "name": decl.spelling,
                        "typestr": typestr,
                        "typestr_special": special,
                        "category": None,
                        "getter": getter,
                        "setter": setter,
                        "attributes": attributes,
                        "availability": self._get_availability(node),
                    }
                )

            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

        if self.verbose:
            print("Added class: " + node.spelling)

    def add_macro(self, macro_name: str):
        self.macro_names.add(macro_name)
        if self.verbose:
            print("Noted macro: " + macro_name)

    def add_category(self, node: Cursor):
        category_name = node.spelling
        for decl in node.get_children():
            if decl.kind == CursorKind.OBJC_CLASS_REF:
                class_name = decl.spelling
                break
        else:
            raise RuntimeError("Category without class reference")

        if (class_name or "") == "":
            print("s")

        class_info: ClassInfo

        if class_name in self.classes:
            class_info = self.classes[class_name]
        else:
            self.classes[class_name] = class_info = {
                "categories": [],
                "methods": [],
                "protocols": set(),
                "properties": [],
                "super": None,
                "availability": None,
            }

        class_info["categories"].append(category_name)

        for proto_ref in node.get_adopted_protocol_nodes():
            proto_str = proto_ref.referenced.spelling
            class_info["protocols"].add(proto_str)

        for decl in node.get_children() or []:
            if (
                decl.kind == CursorKind.OBJC_INSTANCE_METHOD_DECL
                or decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL
            ):
                meth = self.__extract_methoddecl(decl)
                meth["category"] = category_name
                class_info["methods"].append(meth)

            elif decl.kind == CursorKind.OBJC_PROPERTY_DECL:
                typestr, special = self.__get_typestr(decl.type)
                assert typestr is not None

                prop_attr = decl.get_property_attributes()
                getter = setter = None
                attributes = set()
                for item in prop_attr or ():
                    if isinstance(item, str):
                        attributes.add(item)
                    elif item[0] == "getter":
                        getter = item[1]
                    elif item[0] == "setter":
                        setter = item[1]
                    else:
                        raise RuntimeError(f"Unexpected Attribute {attributes}")

                class_info["properties"].append(
                    {
                        "name": decl.spelling,
                        "typestr": typestr,
                        "typestr_special": special,
                        "getter": getter,
                        "setter": setter,
                        "attributes": attributes,
                        "category": category_name,
                        "availability": self._get_availability(decl),
                    }
                )

            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

        if self.verbose:
            print(
                "Added category on class: "
                + str(class_name)
                + " named: "
                + str(category_name)
            )

    # This is largely unmolested from the original parser, but
    # operates on a single define and not the whole
    # file and no longer makes its own invocation, but uses
    # libclang with the PARSE_DETAILED_PROCESSING_RECORD option
    def parse_define(self, define_str: str, curfile: str = "") -> None:
        if not define_str.startswith("#define "):
            define_str = "#define " + define_str
        lines = [define_str]
        for ln_idx, ln in enumerate(lines):

            m = LINE_RE.match(ln)
            if m is not None:
                curfile = m.group(1)

            if curfile is None or self.framework_path not in curfile:
                # Ignore definitions in wrong file
                continue

            if self.only_headers:
                if os.path.basename(curfile) not in self.only_headers:
                    if curfile and "NSSimpleHorizontalTypesetter" in curfile:
                        print("skip", curfile)
                    continue

            m = DEFINE_RE.match(ln)
            if m is not None:
                key = m.group(1)
                if key.startswith("__"):
                    # Ignore private definitions
                    continue

                value = m.group(2)
                if value.endswith("\\"):
                    # Complex macro, ignore
                    print("IGNORE", repr(key), repr(value))
                    continue

                value = value.strip()

                if value in ("static inline",):
                    continue

                if not value:
                    # Ignore empty macros
                    continue

                m = INT_RE.match(value)
                if m is not None:
                    self.literals[key] = {"value": int(m.group(1), 0)}
                    if self.verbose:
                        print(
                            "Added enum_value name: "
                            + key
                            + " value: "
                            + str(self.enum_values[key])
                        )
                    continue

                m = FLOAT_RE.match(value)
                if m is not None:
                    self.literals[key] = {"value": float(m.group(1))}
                    if self.verbose:
                        print(
                            "Added macro literal name: "
                            + key
                            + " value: "
                            + str(self.literals[key])
                        )
                    continue

                m = STR_RE.match(value)
                if m is not None:
                    self.literals[key] = {"unicode": False, "value": m.group(1)}
                    if self.verbose:
                        print(
                            "Added macro literal name: "
                            + key
                            + " value: "
                            + str(self.literals[key]["value"])
                        )
                    continue

                m = UNICODE_RE.match(value)
                if m is not None:
                    self.literals[key] = {"unicode": True, "value": m.group(1)}
                    if self.verbose:
                        print(
                            "Added macro literal name: "
                            + key
                            + " value: "
                            + str(self.literals[key]["value"])
                        )
                    continue

                m = UNICODE2_RE.match(value)
                if m is not None:
                    self.literals[key] = {"value": m.group(1), "unicode": True}
                    if self.verbose:
                        print(
                            "Added macro literal name: "
                            + key
                            + " value: "
                            + str(self.literals[key])
                        )
                    continue

                if value in ("nil", "NULL", "Nil"):
                    self.literals[key] = {"value": None}
                    if self.verbose:
                        print(
                            "Added macro literal name: "
                            + key
                            + " value: "
                            + str(self.literals[key])
                        )
                    continue

                m = ALIAS_RE.match(value)
                if m is not None:
                    if key.startswith("AVAILABLE_MAC_OS_X"):
                        # Ignore availability macros
                        continue

                    if key.endswith("_EXPORT") or key.endswith("_IMPORT"):
                        continue

                    value = m.group(1)
                    if value not in ("extern", "static", "inline", "float"):
                        self.aliases[key] = {"alias": m.group(1), "availability": None}
                        if self.verbose:
                            print(
                                "Added alias name: "
                                + key
                                + " value: "
                                + str(self.aliases[key])
                            )
                    continue

                if value == "(INT_MAX-1)":
                    if self.arch in ("i386", "ppc"):
                        self.literals[key] = {"value": (1 << 31) - 1}
                        if self.verbose:
                            print(
                                "Added enum_values name: "
                                + key
                                + " value: "
                                + str(self.enum_values[key])
                            )
                        continue

                    elif self.arch in ("x86_64", "ppc64", "arm64"):
                        self.literals[key] = {"value": (1 << 63) - 1}
                        if self.verbose:
                            print(
                                "Added enum_values name: "
                                + key
                                + " value: "
                                + str(self.enum_values[key])
                            )
                        continue

                if key in ("NS_DURING", "NS_HANDLER", "NS_ENDHANDLER"):
                    # Classic exception handling in Foundation
                    continue

                if "__attribute__" in value or "__inline__" in value:
                    # It's fairly common to define macros for attributes,
                    # like '#define AM_UNUSED __attribute__((unused))'
                    continue

                if value.startswith("CFUUIDGetConstantUUIDWithBytes("):
                    # Constant CFUUID definitions, used in a number of
                    # frameworks
                    self.expressions[key] = {
                        "expression": value.replace("NULL", "None"),
                        "availability": None,
                    }
                    if self.verbose:
                        print(
                            "Added expression name: "
                            + key
                            + " value: "
                            + str(self.expressions[key])
                        )
                    continue

                m = ERR_SUB_DEFINE_RE.match(value)
                if m is not None:
                    v = FrameworkParser.__parse_int(m.group(1))
                    self.literals[key] = {"value": (v & 0xFFF) << 14}
                    if self.verbose:
                        print(
                            "Added enum_values name: "
                            + key
                            + " value: "
                            + str(self.enum_values[key])
                        )
                    continue

                m = ERR_SYSTEM_DEFINE_RE.match(value)
                if m is not None:
                    v = FrameworkParser.__parse_int(m.group(1))
                    self.literals[key] = {"value": (v & 0x3F) << 26}
                    if self.verbose:
                        print(
                            "Added enum_values name: "
                            + key
                            + " value: "
                            + str(self.enum_values[key])
                        )
                    continue

                m = NULL_VALUE.match(value)
                if m is not None:
                    # For #define's like this:
                    #     #define kDSpEveryContext ((DSpContextReference)NULL)
                    self.literals[key] = {"value": None}
                    if self.verbose:
                        print(
                            "Added literals name: "
                            + key
                            + " value: "
                            + str(self.literals[key])
                        )

                    continue

                m = SC_SCHEMA_RE.match(value)
                if m is not None:
                    # noinspection PyProtectedMember
                    self.externs[key] = {"typestr": objc._C_ID}
                    if self.verbose:
                        print(
                            "Added externs name: "
                            + key
                            + " value: "
                            + str(self.externs[key])
                        )
                    continue

                m = OR_EXPR_RE.match(value)
                if m is not None:
                    self.expressions[key] = {"expression": value, "availability": None}
                    if self.verbose:
                        print(
                            "Added expressions name: "
                            + key
                            + " value: "
                            + str(self.expressions[key])
                        )
                    continue

                m = CALL_VALUE.match(value)
                if m is not None:
                    self.expressions[key] = {"expression": value, "availability": None}
                    if self.verbose:
                        print(
                            "Added expressions name: "
                            + key
                            + " value: "
                            + str(self.expressions[key])
                        )
                    continue

                print("Warning: ignore #define %r %r" % (key, value))

            m = FUNC_DEFINE_RE.match(ln)
            if m is not None:
                proto = m.group(1)
                body = m.group(2).strip()
                if body == "\\":
                    body = body[ln_idx + 1].strip()
                    if body.endswith("\\"):
                        print("Warning: ignore complex function #define %s" % (proto,))
                        continue

                funcdef = "def %s: return %s" % (proto, body)
                try:
                    compile(funcdef, "-", "exec")
                except SyntaxError:
                    pass

                else:
                    key = proto.split("(")[0]
                    self.func_macros[key] = {
                        "definition": funcdef,
                        "availability": None,
                    }
                    if self.verbose:
                        print(
                            "Added func_macros name: "
                            + key
                            + " value: "
                            + self.func_macros[key]["definition"]
                        )

    @staticmethod
    def __node_is_cferror_ptr(node: Cursor) -> bool:
        """

        @type node: Cursor
        @param node:
        @rtype : bool
        """
        if isinstance(node, Cursor):
            node = node.type

        if node.kind != TypeKind.POINTER:
            return False

        node = node.get_pointee()

        if getattr(node, "spelling", None) == "CFErrorRef":
            return True

        return False

    @staticmethod
    def __index_of_printf_format_arg(node: Cursor) -> typing.Optional[int]:
        """

        @type node: Cursor
        @param node:
        @rtype : int
        """
        index_of_format_arg = None
        for child in node.get_children():
            if child.kind.is_attribute():
                has_format_attr = False
                is_printf_not_scanf = False
                for token in child.get_tokens():
                    string = token.spelling
                    if string == "format":
                        has_format_attr = True

                    elif string in [
                        "printf",
                        "__printf__",
                        "NSString",
                        "__NSString__",
                        "CFString",
                        "__CFString__",
                    ]:
                        is_printf_not_scanf = True

                    elif string == "scanf":
                        is_printf_not_scanf = False

                    elif (
                        has_format_attr
                        and is_printf_not_scanf
                        and index_of_format_arg is None
                    ):
                        try:
                            # attribute syntax uses counting
                            # numbers,
                            # we use array indexes
                            val = int(string) - 1

                        except ValueError:
                            index_of_format_arg = None

                        else:
                            index_of_format_arg = val if val >= 0 else None

        return index_of_format_arg

    def __extract_function_like_thing(self, thing: Cursor) -> CallbackInfo:
        """

        @rtype : dict
        """
        if isinstance(thing, Type):
            print("Attributed")
            if thing.kind == TypeKind.ATTRIBUTED:
                thing = thing.modified_type
            if thing.kind == TypeKind.TYPEDEF:
                thing = thing.declaration.underlying_typedef_type.get_pointee()
        print("K", thing.kind)
        return_type = thing.get_result()
        assert return_type.kind != TypeKind.INVALID

        result: CallbackInfo = {}

        typestr = self.__get_typestr(return_type)[0]
        assert typestr is not None

        result["retval"] = {"typestr": typestr}

        if return_type.nullability == NullabilityKind.NONNULL:
            result["retval"]["null_accepted"] = False
        elif return_type.nullability == NullabilityKind.NULLABLE:
            result["retval"]["null_accepted"] = True

        result["args"] = []

        arg_types_iter = thing.argument_types()
        arg_types = [] if arg_types_iter is None else list(arg_types_iter)

        if thing.is_function_variadic():
            result["variadic"] = True

        for arg in arg_types:
            assert arg.kind != TypeKind.INVALID
            typestr = self.__get_typestr(arg)[0]
            assert typestr is not None

            arginfo: CallbackArgInfo
            arginfo = {"typestr": typestr}

            if arg.nullability == NullabilityKind.NONNULL:
                arginfo["null_accepted"] = False
            elif arg.nullability == NullabilityKind.NULLABLE:
                arginfo["null_accepted"] = True

            if arg.kind == TypeKind.BLOCKPOINTER:
                arginfo["callable"] = self.__extract_block2(arg.type)

            if arg.looks_like_function:
                arginfo["callable"] = self.__extract_function2(arg.type)

            result["args"].append(arginfo)

        return result

    def __extract_block(self, block: Cursor) -> CallbackInfo:
        """

        @rtype : dict
        """
        if isinstance(block, Cursor):
            block = block.type

        assert isinstance(block, Type), "huh"

        if isinstance(block, Type) and block.kind == TypeKind.BLOCKPOINTER:
            block = block.get_pointee()

        return self.__extract_function_like_thing(block)

    # The three "...2" methods below are functionally identical except
    # for (a) type annotations and (b) not suppporting callbacks themselfes.
    #

    def __extract_function_like_thing2(self, thing: Cursor) -> CallbackInfo2:
        """

        @rtype : dict
        """
        return_type = thing.get_result()
        assert return_type.kind != TypeKind.INVALID

        result: CallbackInfo2 = {}

        typestr = self.__get_typestr(return_type)[0]
        assert typestr is not None

        result["retval"] = {"typestr": typestr}

        if return_type.nullability == NullabilityKind.NONNULL:
            result["retval"]["null_accepted"] = False
        elif return_type.nullability == NullabilityKind.NULLABLE:
            result["retval"]["null_accepted"] = True

        result["args"] = []

        arg_types_iter = thing.argument_types()
        arg_types = [] if arg_types_iter is None else list(arg_types_iter)

        if thing.is_function_variadic():
            result["variadic"] = True

        for arg in arg_types:
            assert arg.kind != TypeKind.INVALID
            typestr = self.__get_typestr(arg)[0]
            assert typestr is not None

            arginfo: CallbackArgInfo2
            arginfo = {"typestr": typestr}

            if arg.nullability == NullabilityKind.NONNULL:
                arginfo["null_accepted"] = False
            elif arg.nullability == NullabilityKind.NULLABLE:
                arginfo["null_accepted"] = True

            if arg.kind == TypeKind.BLOCKPOINTER:
                print("2nd level nested blocks not supported")
                # arginfo["callable"] = self.__extract_block2(arg.type)

            if arg.looks_like_function:
                print("2nd level nested callbacks not supported")
                # arginfo["callable"] = self.__extract_function2(arg_type)

            result["args"].append(arginfo)

        return result

    def __extract_block2(self, block: Cursor) -> CallbackInfo2:
        """

        @rtype : dict
        """
        if isinstance(block, Cursor):
            block = block.type

        assert isinstance(block, Type), "huh"

        if isinstance(block, Type) and block.kind == TypeKind.BLOCKPOINTER:
            block = block.get_pointee()

        return self.__extract_function_like_thing2(block)

    def __extract_function2(self, func: Cursor) -> CallbackInfo2:
        """

        @rtype : dict
        """
        if isinstance(func, Cursor):
            func = func.type

        assert isinstance(func, Type), "huh"

        # func = func.get_canonical()
        # Going straight to the canonical robs us of "special" conversions
        if func.kind == TypeKind.TYPEDEF:
            td = func.declaration
            ult = None if td is None else td.underlying_typedef_type_valid
            if ult:
                func = ult
            else:
                func = func.get_canonical()

        while func.kind == TypeKind.POINTER:
            func = func.get_pointee()

        return self.__extract_function_like_thing2(func)

    def __extract_function(self, func: Cursor) -> CallbackInfo:
        """

        @rtype : dict
        """
        if isinstance(func, Cursor):
            func = func.type
        orig_func = func

        assert isinstance(func, Type), "huh"

        # func = func.get_canonical()
        # Going straight to the canonical robs us of "special" conversions
        if func.kind == TypeKind.TYPEDEF:
            td = func.declaration
            ult = None if td is None else td.underlying_typedef_type_valid
            if ult:
                func = ult
            else:
                func = func.get_canonical()

        while func.kind == TypeKind.POINTER:
            func = func.get_pointee()

        try:
            return self.__extract_function_like_thing(func)
        except AssertionError:
            from .clang_tools import dump_type

            dump_type(orig_func)
            raise

    def __extract_methoddecl(self, decl: Cursor) -> MethodInfo:
        """

        @rtype : dict
        """
        typestr: typing.Optional[bytes]

        if decl.result_type.valid_type is None:
            typestr, special = objc._C_ID, False
        else:
            typestr, special = self.__get_typestr(decl.result_type.valid_type)
            from .clang_tools import dump_type

            if typestr is None:
                dump_type(decl.result_type)
            assert (
                typestr is not None
            ), f"typestr is None for {decl.result_type.spelling!r}"

        retval: ArgInfo = {"typestr": typestr, "typestr_special": special}

        type_name = self.get_typename(decl.result_type)
        if type_name is not None:
            retval["type_name"] = type_name

        if decl.result_type.nullability == NullabilityKind.NONNULL:
            retval["null_accepted"] = False
        elif decl.result_type.nullability == NullabilityKind.NULLABLE:
            retval["null_accepted"] = True

        meth: MethodInfo = {
            "selector": decl.spelling,
            "class_method": bool(decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL),
            "retval": retval,
            "args": [],
            "availability": self._get_availability(decl),
        }

        if decl.is_variadic:
            meth["variadic"] = True

        for a in decl.get_arguments():
            if a is None:
                typestr, special = objc._C_ID, False
            else:
                typestr, special = self.__get_typestr(a.type)
                assert typestr is not None

            arg: ArgInfo = {
                "typestr": typestr,
                "typestr_special": special,
                "name": a.spelling,
            }

            if a.type.nullability == NullabilityKind.NONNULL:
                arg["null_accepted"] = False
            elif a.type.nullability == NullabilityKind.NULLABLE:
                arg["null_accepted"] = True

            type_name = self.get_typename(a.type)
            if type_name is not None:
                arg["type_name"] = type_name

            meth["args"].append(arg)

        return meth

    def __includes_string(self) -> str:
        """

        @rtype : str
        """
        include_string = ""
        for hdr in self.preheaders:
            include_string += "#import <%s>\n" % (hdr,)

        include_string += "#import <%s>\n" % (self.start_header,)

        for hdr in self.additional_headers:
            include_string += "#import <%s/%s>" % (self.framework, hdr)

        for hdr in self.extraheaders:
            include_string += "#import <%s>\n" % (hdr,)

        return include_string

    @staticmethod
    def __parse_int(value: typing.Union[int, str]) -> int:
        """
        Parse a C integer literal and return its value

        @type value: str
        @param value:
        @rtype : int
        """
        if isinstance(value, int):
            return value
        value = value.lower().rstrip("l").rstrip("u")
        return int(value, 0)

    def get_typename(self, obj: typing.Union[Cursor, Type]) -> typing.Optional[str]:
        """
        Return the name of a type when that name is relevant
        for PyObjC (class name, typedef name).
        """
        is_cursor = isinstance(obj, Cursor)
        is_type = (not is_cursor) and isinstance(obj, Type)

        if is_type:
            if obj.kind == TypeKind.ATTRIBUTED:
                return self.get_typename(obj.modified_type)

            primitive_objc_type_for_arch = obj.kind.objc_type_for_arch(self.arch)
            if primitive_objc_type_for_arch == objc._C_ID:
                # This doesn't work yet: return 'id' instead of the
                # name of the class
                return obj.spelling.rstrip("*").strip()

            if obj.kind == TypeKind.TYPEDEF:
                # This needs to check if the typedef is for an interesting type:
                # - struct
                # - enum
                return obj.spelling.lstrip("const ")

            # Missing: Pointer to one of the above should return the name of
            # the pointed-to type.

            return None

        else:
            return None

    def __get_typestr(
        self, obj: Cursor, exogenous_name: typing.Optional[str] = None
    ) -> typing.Tuple[typing.Optional[bytes], bool]:
        """
        There was a bunch of logic around "Special", but in practice,
        the only things that are special are ObjC BOOL and
        anonymous structs in typedef i.e.
            typedef struct { int foo; } MyStruct;
        (Technically that struct is anonymous
        because there's no tag, like this:
            typedef struct _MyStruct { int foo; } MyStruct;

        @type obj: [Cursor, Type]
        @param obj:
        @type exogenous_name: str
        @param exogenous_name:
        @rtype : ( str, bool )
        """
        assert exogenous_name is None or "union" not in exogenous_name
        is_cursor = isinstance(obj, Cursor)
        is_type = (not is_cursor) and isinstance(obj, Type)
        if not is_cursor and not is_type:
            return None, False

        # get the map of arch to typestr
        if not hasattr(obj, "_typestr_cache"):
            obj._typestr_cache = {}

        # see if we have an entry for this arch
        cached_type_str = obj._typestr_cache.get(self.arch)

        # if we don't, make one
        if not cached_type_str:
            if is_cursor:
                cached_type_str = self.__typestr_from_node(
                    obj, exogenous_name=exogenous_name
                )
            elif is_type:
                cached_type_str = self.__typestr_from_type(
                    typing.cast(Type, obj), exogenous_name=exogenous_name
                )

            assert cached_type_str is not None
            obj._typestr_cache[self.arch] = cached_type_str

        # return it
        return cached_type_str

    # noinspection PyProtectedMember
    def __typestr_from_type(
        self,
        clang_type: Type,
        exogenous_name: typing.Optional[str] = None,
        is_pointee=False,
        seen: typing.Optional[typing.Set[typing.Any]] = None,
    ) -> typing.Tuple[typing.Optional[bytes], bool]:
        """

        @type clang_type: Type
        @param clang_type:
        @type exogenous_name: str
        @param exogenous_name:
        @return: @raise UnsuportedClangOptionError:
        @rtype : ( str, bool )
        """
        assert isinstance(clang_type, Type)
        if seen is None:
            seen = set()

        if clang_type in seen:
            raise SelfReferentialTypeError("Nested type")

        seen.add(clang_type)
        try:
            typestr: typing.Optional[bytes] = None
            special: bool = False
            assert clang_type is not None, "bad type"

            # Check for built in types
            primitive_objc_type_for_arch = clang_type.kind.objc_type_for_arch(self.arch)
            if primitive_objc_type_for_arch:  # should handle all builtins
                typestr = primitive_objc_type_for_arch
                special = (
                    False  # used to be "typestr in self._special" - That seems wrong
                )

            # OK, it's not a built-in type

            # CXType_Invalid = 0,
            elif clang_type.kind == TypeKind.INVALID:
                pass  # no typestr for this

            # CXType_Unexposed = 1,
            elif clang_type.kind == TypeKind.UNEXPOSED:
                # Opaque struct?
                typedecl = clang_type.get_declaration()
                if typedecl.kind != CursorKind.NO_DECL_FOUND:
                    typestr, special = self.__typestr_from_node(
                        typedecl,
                        exogenous_name=exogenous_name,
                        seen=seen,
                        is_pointee=is_pointee,
                    )
                elif clang_type.looks_like_function:
                    typestr = b"?"
                    special = False
                else:
                    canonical = clang_type.get_canonical()
                    if canonical.kind != TypeKind.UNEXPOSED:
                        return self.__typestr_from_type(
                            canonical,
                            exogenous_name=exogenous_name,
                            seen=seen,
                            is_pointee=is_pointee,
                        )

                    typestr = None
                    special = False

            # CXType_Complex = 100,
            elif clang_type.kind == TypeKind.COMPLEX:
                raise UnsuportedClangOptionError(clang_type.kind)

            # CXType_Pointer = 101,
            elif clang_type.kind == TypeKind.POINTER:
                pointee = clang_type.get_pointee()
                t, special = self.__typestr_from_type(
                    pointee, exogenous_name=exogenous_name, seen=seen, is_pointee=True
                )
                typestr = objc._C_PTR + (t if t is not None else b"")

            # CXType_BlockPointer = 102,
            elif clang_type.kind == TypeKind.BLOCKPOINTER:
                typestr = objc._C_ID + objc._C_UNDEF

            # CXType_LValueReference = 103,
            elif clang_type.kind == TypeKind.LVALUEREFERENCE:
                raise UnsuportedClangOptionError(clang_type.kind)

            # CXType_RValueReference = 104,
            elif clang_type.kind == TypeKind.RVALUEREFERENCE:
                raise UnsuportedClangOptionError(clang_type.kind)

            # CXType_Record = 105,
            elif clang_type.kind == TypeKind.RECORD:
                typedecl = clang_type.get_declaration()
                assert (
                    CursorKind.NO_DECL_FOUND != typedecl.kind
                ), "Couldn't find declaration for non-primitive type"
                exogenous_name = (
                    exogenous_name
                    if exogenous_name
                    else getattr(typedecl.type, "spelling", None)
                )
                if "union" in exogenous_name:
                    # Nested anonymous unions cause problems.
                    exogenous_name = None

                typestr, special = self.__typestr_from_node(
                    typedecl,
                    exogenous_name=exogenous_name,
                    seen=seen,
                    is_pointee=is_pointee,
                )

            # CXType_Enum = 106,
            elif clang_type.kind == TypeKind.ENUM:
                typedecl = clang_type.get_declaration()
                assert (
                    CursorKind.NO_DECL_FOUND != typedecl.kind
                ), "Couldn't find declaration for non-primitive type"
                typestr, special = self.__typestr_from_node(
                    typedecl, exogenous_name=exogenous_name, seen=seen
                )

            # CXType_Typedef = 107,
            elif clang_type.kind == TypeKind.TYPEDEF:
                canonical_type = clang_type.get_canonical()

                if clang_type.ever_defines_to("BOOL"):
                    # Give a hint to look into it if things don't add up
                    assert canonical_type.kind == TypeKind.SCHAR
                    typestr = objc._C_NSBOOL
                    special = True
                elif clang_type.ever_defines_to("Boolean"):
                    # Give a hint to look into it if things don't add up
                    assert canonical_type.kind == TypeKind.UCHAR
                    typestr = objc._C_NSBOOL
                    special = True

                elif clang_type.ever_defines_to("CFTypeRef"):
                    typestr = objc._C_ID

                elif clang_type.ever_defines_to("UniChar"):
                    typestr = objc._C_UNICHAR

                else:
                    # In an ideal world, we would go right to canonical
                    # here, but we can't because we treat some typedefs
                    # like BOOL and Boolean and UniChar specially.
                    next_type = clang_type.next_typedefed_type
                    if not exogenous_name:
                        if clang_type.declaration is not None:
                            exogenous_name = clang_type.declaration.spelling
                    if next_type:
                        typestr, special = self.__typestr_from_type(
                            next_type,
                            exogenous_name=exogenous_name,
                            seen=seen,
                            is_pointee=is_pointee,
                        )
                    else:
                        typestr, special = self.__typestr_from_type(
                            canonical_type,
                            exogenous_name=exogenous_name,
                            seen=seen,
                            is_pointee=is_pointee,
                        )

                    assert typestr is not None

                    if typestr == b"^?" and clang_type.looks_like_function:
                        # Maybe we can do better in the future?
                        pass

                    if b"{" in typestr:
                        # hopefully checking the string is cheaper
                        # than fetching the declaration
                        canonical_type_decl = canonical_type.declaration
                        # anonymous struct... we treat these as "special" for some reason
                        if (
                            canonical_type_decl
                            and canonical_type_decl.kind == CursorKind.STRUCT_DECL
                            and canonical_type_decl.spelling == ""
                        ):
                            special = True

            # CXType_ObjCInterface = 108,
            elif clang_type.kind == TypeKind.OBJCINTERFACE:
                pass  # no typestr for this

            # CXType_ObjCObjectPointer = 109,
            elif clang_type.kind == TypeKind.OBJCOBJECTPOINTER:
                typestr = objc._C_ID

            # CXType_FunctionNoProto = 110,
            elif clang_type.kind == TypeKind.FUNCTIONNOPROTO:
                typestr = b""

            # CXType_FunctionProto = 111,
            elif clang_type.kind == TypeKind.FUNCTIONPROTO:
                typestr = objc._C_UNDEF

            # CXType_ConstantArray = 112,
            elif clang_type.kind in [
                TypeKind.CONSTANTARRAY,
                TypeKind.INCOMPLETEARRAY,
                TypeKind.VECTOR,
            ]:
                result = []
                result.append(objc._C_ARY_B)
                dim = clang_type.element_count
                if dim is None or int(dim) is None or int(dim) == 0:
                    t = b""
                    s = False
                    element_type = clang_type.element_type
                    if element_type:
                        t, s = self.__typestr_from_type(
                            element_type, exogenous_name=exogenous_name
                        )
                        assert t is not None
                    return objc._C_PTR + t, s
                else:
                    result.append(b"%d" % (dim,))

                element_type = clang_type.element_type
                t, s = self.__typestr_from_type(
                    element_type, exogenous_name=exogenous_name, seen=seen
                )
                assert t is not None
                result.append(t)
                if s:
                    special = True

                result.append(objc._C_ARY_E)
                typestr = b"".join(result)

            # CXType_Vector = 113,
            elif clang_type.kind == TypeKind.VECTOR:
                pass  # handled with CONSTANTARRAY above

            # CXType_IncompleteArray = 114,
            elif clang_type.kind == TypeKind.INCOMPLETEARRAY:
                pass  # handled with CONSTANTARRAY above

            # CXType_VariableArray = 115,
            elif clang_type.kind == TypeKind.VARIABLEARRAY:
                raise UnsuportedClangOptionError(clang_type.kind)

            # CXType_DependentSizedArray = 116,
            elif clang_type.kind == TypeKind.DEPENDENTSIZEDARRAY:
                raise UnsuportedClangOptionError(clang_type.kind)

            # CXType_MemberPointer = 117,
            elif clang_type.kind == TypeKind.MEMBERPOINTER:
                raise UnsuportedClangOptionError(clang_type.kind)

            elif clang_type.kind == TypeKind.ATTRIBUTED:
                return self.__typestr_from_type(
                    clang_type.modified_type, exogenous_name, seen=seen
                )

            else:
                typedecl = clang_type.get_declaration()
                if typedecl.kind == CursorKind.NO_DECL_FOUND:
                    return b"?", True
                assert (
                    CursorKind.NO_DECL_FOUND != typedecl.kind
                ), "Couldn't find declaration for non-primitive type"
                typestr, special = self.__typestr_from_node(
                    typedecl, exogenous_name=exogenous_name, seen=seen
                )

            return typestr, special

        finally:
            seen.remove(clang_type)

    __typestr_from_node_ignore = [
        CursorKind.OBJC_PROTOCOL_DECL,
        CursorKind.OBJC_INTERFACE_DECL,
        CursorKind.OBJC_CATEGORY_DECL,
        CursorKind.OBJC_IMPLEMENTATION_DECL,
        CursorKind.OBJC_CATEGORY_IMPL_DECL,
        CursorKind.OBJC_SUPER_CLASS_REF,
        CursorKind.OBJC_CLASS_REF,
        CursorKind.OBJC_PROTOCOL_REF,
    ]

    __typestr_from_node_use_type = [
        CursorKind.TYPE_REF,
        CursorKind.OBJC_CLASS_REF,
        CursorKind.UNEXPOSED_DECL,
        CursorKind.INTEGER_LITERAL,
        CursorKind.FLOATING_LITERAL,
        CursorKind.STRING_LITERAL,
        CursorKind.CHARACTER_LITERAL,
        CursorKind.FIELD_DECL,
        CursorKind.ENUM_CONSTANT_DECL,
        CursorKind.FUNCTION_DECL,
        CursorKind.VAR_DECL,
        CursorKind.PARM_DECL,
        CursorKind.OBJC_PROPERTY_DECL,
        CursorKind.OBJC_IVAR_DECL,
    ]

    # noinspection PyProtectedMember
    def __typestr_from_node(
        self,
        node: Cursor,
        exogenous_name: typing.Optional[str] = None,
        is_pointee: bool = False,
        seen: typing.Optional[typing.Set[typing.Any]] = None,
    ) -> typing.Tuple[typing.Optional[bytes], bool]:
        """

        @type node: Cursor
        @param node:
        @type exogenous_name: str
        @param exogenous_name:
        @rtype : ( str, bool )
        """
        assert isinstance(node, Cursor)
        if seen is None:
            seen = set()

        if node in seen:
            raise SelfReferentialTypeError("Recursive type")

        seen.add(node)

        try:

            special: bool = False
            typestr: typing.Optional[bytes] = node.objc_type_encoding
            if typestr != b"?" and typestr != b"" and typestr is not None:
                return typestr, special

            # bail out of irrelevant cases

            if (
                node.kind.is_translation_unit()
                or node.kind.is_attribute()
                or node.kind.is_invalid()
                or node.kind.is_statement()
                or node.kind in self.__typestr_from_node_ignore
            ):
                return None, special

            # drill down into the type
            if (
                node.kind in self.__typestr_from_node_use_type
                or node.kind.is_expression()
            ):
                clang_type = node.type
                typestr, special = self.__typestr_from_type(
                    clang_type, exogenous_name=exogenous_name, seen=seen
                )
                assert isinstance(typestr, bytes)
            # follow typedefs
            elif node.kind == CursorKind.TYPEDEF_DECL:
                underlying_type = node.underlying_typedef_type
                typestr, special = self.__typestr_from_type(
                    underlying_type, exogenous_name=exogenous_name, seen=seen
                )
                assert isinstance(typestr, bytes)
            # enums
            elif node.kind == CursorKind.ENUM_DECL:
                clang_type = node.enum_type
                typestr, special = self.__typestr_from_type(
                    clang_type, exogenous_name=exogenous_name, seen=seen
                )
            # structs
            elif node.kind == CursorKind.STRUCT_DECL:
                result = [objc._C_STRUCT_B]
                if node.spelling is None or node.spelling == "":
                    if exogenous_name is not None:
                        result.append(b"_" + exogenous_name.encode())
                        assert not exogenous_name.startswith(
                            "const"
                        ), "Qualifiers leaking into names!"
                        special = True
                else:
                    assert not node.spelling.startswith(
                        "const"
                    ), "Qualifiers leaking into names!"
                    result.append(node.spelling.encode())

                result.append(b"=")

                try:
                    for c in node.get_children():
                        if c.kind.is_attribute():
                            continue
                        is_bf = c.is_bitfield()
                        bf_width = -1 if not is_bf else c.get_bitfield_width()
                        if is_bf and bf_width != -1:
                            result.append(b"b%d" % (bf_width,))
                        else:
                            c_type = c.type
                            t, s = self.__typestr_from_type(
                                c_type, seen=seen, is_pointee=is_pointee
                            )
                            if s:
                                special = True
                            assert t is not None

                            result.append(t)
                except SelfReferentialTypeError:
                    result = result[: result.index(b"=")]

                result.append(objc._C_STRUCT_E)
                typestr, special = b"".join(result), special
            # unions
            elif node.kind == CursorKind.UNION_DECL:
                result = [objc._C_UNION_B]
                if node.spelling is None or node.spelling == "":
                    assert (
                        exogenous_name is None or "union" not in exogenous_name
                    ), exogenous_name
                    if exogenous_name is not None:
                        result.append(b"_" + exogenous_name.encode())
                        assert not exogenous_name.startswith(
                            "const"
                        ), "Qualifiers leaking into names!"
                        special = True
                else:
                    assert "union" not in node.spelling, node.spelling
                    assert not node.spelling.startswith(
                        "const"
                    ), "Qualifiers leaking into names!"
                    result.append(node.spelling.encode())

                result.append(b"=")

                for c in node.get_children():
                    if c.kind.is_attribute():
                        continue

                    t, s = self.__typestr_from_node(c, seen=seen)
                    assert isinstance(t, bytes)
                    if s:
                        special = True
                    if t is not None:
                        result.append(t)

                result.append(objc._C_UNION_E)
                typestr, special = b"".join(result), special
            else:
                # try drilling down to canonical
                canonical = node.canonical
                if node != canonical:
                    return self.__typestr_from_node(
                        node.canonical, exogenous_name=exogenous_name, seen=seen
                    )
                else:
                    print("Unhandled node:", node)

            assert isinstance(typestr, bytes)
            return typestr, special

        finally:
            seen.remove(node)


class UnsuportedClangOptionError(Exception):
    """
    Exception for flagging unsupported/un-implemented language constructs.

    """

    def __init__(self, unsupported_thing=None):
        self.unsupported_thing = unsupported_thing


class DefinitionVisitor(AbstractClangVisitor):
    """
    A AbstractClangVisitor that calls back to the framework parser when it
    locates interesting definitions.
    """

    def __init__(self, parser):
        if parser is None:
            raise ValueError(parser)

        self._parser = parser
        # Record all records seen in header files, even
        # if they are not in a framework we're scanning.
        # This way we can emit the expected metadata
        # for "typedef CGRect NSRect" (as used in the Foundation
        # headers on x86_64)
        self.__all_structs = {}

    def visit(self, node: Cursor):
        if not self._parser.should_process_cursor(node):
            return

        super(DefinitionVisitor, self).visit(node)

    def visit_var_decl(self, node: Cursor):
        linkage = node.linkage
        if linkage == LinkageKind.EXTERNAL or linkage == LinkageKind.UNIQUE_EXTERNAL:
            self._parser.add_extern(node.spelling, node)

        elif linkage == LinkageKind.INTERNAL:
            self._parser.add_static_const(node.spelling, node)

    def visit_enum_decl(self, node: Cursor):
        self._parser.add_enumeration(node)

    def visit_struct_decl(self, node: Cursor):
        self.descend(node)
        assert "union" not in node.type.spelling
        self.__all_structs[getattr(node.type, "spelling", None)] = node.type

    def visit_objc_protocol_decl(self, node: Cursor):
        self.descend(node)
        self._parser.add_protocol(node)

    def visit_function_decl(self, node: Cursor):
        self._parser.add_function(node)

    def visit_typedef_decl(self, node: Cursor):
        self.descend(node)

        typedef_type = node.type
        typedef_name = typedef_type.declaration.spelling

        underlying_type = node.underlying_typedef_type
        underlying_name = getattr(underlying_type, "spelling", None)
        if "union" in underlying_name:
            if underlying_name.startswith("union "):
                underlying_name = underlying_name[6:].lstrip()

            else:
                from .clang_tools import dump_node

                dump_node(node)
                assert 0, underlying_name

        # Add typedef to parser
        self._parser.add_typedef(typedef_name, underlying_name)

        # Ignore typedefs that resolve to function declarations
        if (
            underlying_type.declaration is not None
            and underlying_type.declaration.kind == CursorKind.FUNCTION_DECL
        ):
            return

        if underlying_name in self._parser.cftypes:
            self._parser.add_alias(typedef_name, underlying_name)

        if underlying_name in self.__all_structs:
            self._parser.add_struct(typedef_name, self.__all_structs[underlying_name])

        canonical_type = typedef_type.get_canonical()
        if canonical_type and canonical_type.kind == TypeKind.RECORD:
            self._parser.add_struct(typedef_name, underlying_type)

        if underlying_type.kind == TypeKind.POINTER:
            pointee_type = underlying_type.get_pointee()
            pointee_type_decl = None if not pointee_type else pointee_type.declaration
            if (
                pointee_type_decl is not None
                and pointee_type_decl.kind == CursorKind.STRUCT_DECL
            ):
                if pointee_type_decl.spelling.startswith("__"):
                    self._parser.add_cftype(typedef_name, typedef_type)

    def visit_objc_category_decl(self, node: Cursor):
        self.descend(node)

        self._parser.add_category(node)

        if node.get_is_informal_protocol():
            self._parser.add_informal_protocol(node)

    def visit_objc_interface_decl(self, node: Cursor):
        self.descend(node)
        self._parser.add_class(node)

    def visit_macro_definition(self, node: Cursor):
        macro = node.reconstitute_macro()
        file_name = (
            "" if node.extent.start.file is None else node.extent.start.file.name
        )
        self._parser.parse_define(macro, file_name)


LINE_RE = re.compile(r'^# \d+ "([^"]*)" ')
DEFINE_RE = re.compile(r"#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.*)$")
INT_RE = re.compile(
    r"^\(?(?:\([A-Za-z][A-Za-z0-9]*(?:\s+long)?\))?((?:-?0[Xx][0-9A-Fa-f]+)|(?:-?\d+))[UL]*\)?$"  # noqa: B950
)
FLOAT_RE = re.compile(r"^\(?(?:\([A-Za-z][A-Za-z0-9]*\))?\(?([-]?\d+\.\d+)\)?\)?$")
STR_RE = re.compile(r'^"(.*)"$')
UNICODE_RE = re.compile(r'^@"(.*)"$')
UNICODE2_RE = re.compile(r'^CFSTR\("(.*)"\)$')
ALIAS_RE = re.compile(r"^(?:\(\s*[A-Za-z0-9_]+\s*\))?\s*([A-Za-z_][A-Za-z0-9_]*)$")
NULL_VALUE = re.compile(r"\(\([A-Za-z0-9]+\)NULL\)")
CALL_VALUE = re.compile(r"^[A-Za-z0-9]+\([A-Za-z0-9]*\)$")
FUNC_DEFINE_RE = re.compile(
    r"#\s*define\s+([A-Za-z_][A-Za-z0-9_]*\([A-Za-z0-9_, ]*\))\s+(.*)$"
)
ERR_SUB_DEFINE_RE = re.compile(r"err_sub\s*\(\s*((?:0x)?[0-9a-fA-F]+)\s*\)")
ERR_SYSTEM_DEFINE_RE = re.compile(r"err_system\s*\(\s*((?:0x)?[0-9a-fA-F]+)\s*\)")
SC_SCHEMA_RE = re.compile(r"SC_SCHEMA_KV\s*\(\s*([A-Za-z0-9_]*)\s*,.*\)")
OR_EXPR_RE = re.compile(r"\([A-Za-z0-9]*(\s*\|\s*[A-Za-z0-9]*)*\)")


def debug_break():
    if False:
        pass
