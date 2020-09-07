"""
Utility module for parsing the header files in a framework and extracting
interesting definitions.
"""
import os
import platform
import re
import sys
import typing
from dataclasses import replace

import objc

from . import util
from .clang import (
    Config,
    Cursor,
    CursorKind,
    Index,
    LinkageKind,
    NullabilityKind,
    ObjCDeclQualifier,
    TranslationUnit,
    TranslationUnitLoadError,
    Type,
    TypeKind,
)
from .datamodel import (
    AliasInfo,
    ArgInfo,
    AvailabilityInfo,
    CallbackArgInfo,
    CallbackArgInfo2,
    CallbackInfo,
    CallbackInfo2,
    CFTypeInfo,
    ClassInfo,
    EnumInfo,
    EnumTypeInfo,
    ExpressionInfo,
    ExternInfo,
    FrameworkMetadata,
    FunctionInfo,
    FunctionMacroInfo,
    LiteralInfo,
    MethodInfo,
    PropertyInfo,
    ProtocolInfo,
    ReturnInfo,
    StructInfo,
)

Config.set_library_path(
    "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/"  # noqa: B950
)

from objective.metadata.clanghelpers import (  # isort:skip  # noqa: E402
    AbstractClangVisitor,
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

        self.headers: typing.Set[str] = util.sorted_set()

        self.meta = FrameworkMetadata()

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
        for class_info in self.meta.classes.values():
            class_info.methods.sort(key=lambda item: (item.selector, item.class_method))

        for prot_info in self.meta.formal_protocols.values():
            prot_info.methods.sort(key=lambda item: (item.selector, item.class_method))

        for prot_info in self.meta.informal_protocols.values():
            prot_info.methods.sort(key=lambda item: (item.selector, item.class_method))

        return {
            "framework": self.framework,
            "arch": self.arch,
            "sdk": self.sdk,
            "release": platform.mac_ver()[0],
            "headers": sorted(self.headers),
            "definitions": self.meta.to_dict(),
        }

    @property
    def exceptions(self) -> dict:  # incomplete type info
        return {"definitions": {}}

    # Old implementation, needs to be modernized and might be
    # done differently
    """
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
    """

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
        self.meta.aliases[alias] = AliasInfo(alias=value)

    # def add_typedef(self, name: str, typedef: str) -> None:
    # assert isinstance(name, str)
    # assert isinstance(typedef, str)
    # self.typedefs[name] = typedef

    def add_enumeration(self, node: Cursor) -> None:
        # Need to do something with availability information
        if node.spelling is None or node.spelling == "":
            name = "<anon>"
        else:
            name = node.spelling

        value_count = 0

        if node.kind == CursorKind.ENUM_CONSTANT_DECL:
            self.meta.literals[name] = LiteralInfo(value=node.enum_value)
            value_count = 1

        elif node.kind == CursorKind.ENUM_DECL:
            typestr = self.__get_typestr(node)[0]
            assert typestr is not None
            self.meta.enum_type[node.spelling] = EnumTypeInfo(
                typestr=typestr, availability=self._get_availability(node)
            )
            for val in node.get_children():
                if val.kind != CursorKind.ENUM_CONSTANT_DECL:
                    continue

                # Add the enum to the name -> value mapping
                val_name = val.spelling
                self.meta.enum[val_name] = EnumInfo(
                    value=val.enum_value,
                    enum_type=node.spelling,
                    availability=self._get_availability(val),
                )

                # Check to see if there's also an alias inherent in this declaration
                children = [x for x in val.get_children() if x.kind.is_expression()]
                if len(children) == 1:
                    referenced_decl = children[0].referenced_distinct
                    if (
                        referenced_decl
                        and referenced_decl.kind == CursorKind.ENUM_CONSTANT_DECL
                    ):
                        other_name = referenced_decl.spelling
                        if len(other_name or ""):
                            self.meta.aliases[val_name] = AliasInfo(
                                alias=other_name,
                                enum_type=node.spelling,
                                availability=self._get_availability(val),
                            )
                else:
                    assert len(children) == 0, children

                value_count += 1

        if self.verbose:
            print(f"Added enumeration: {name} with {value_count} values")

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
        self.meta.cftypes[name] = CFTypeInfo(typestr=typestr)
        if self.verbose:
            print(f"Added CFType: {name}")

    def add_struct(self, name: str, struct_type: Type) -> None:
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

        self.meta.structs[name] = StructInfo(
            typestr=typestr,
            fieldnames=fieldnames,
            typestr_special=special,
            ignore=(typestr is not None and b"^?" in typestr),
        )

        if self.verbose:
            print(f"Added struct: {name}")

    def add_static_const(self, name: str, node: Cursor) -> None:
        node_type = node.type
        assert node_type is not None
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
                        self.meta.literals[name] = LiteralInfo(
                            value=int(m.token_string)
                        )
                        if self.verbose:
                            print(f"Added static const {name}")

                elif m.kind == CursorKind.FLOATING_LITERAL:
                    if m.token_string == "":
                        print(f"Ignore {name!r}: empty token string")
                    else:
                        self.meta.literals[name] = LiteralInfo(
                            value=float(m.token_string)
                        )
                        if self.verbose:
                            print(f"Added static const {name}")

    def add_extern(self, name: str, node: Cursor) -> None:
        node_type = node.type
        assert node_type is not None
        typestr, special = self.__get_typestr(node_type)
        assert typestr is not None

        if not special and b"?" == typestr:
            clang_typestr = node.objc_type_encoding
            if len(str(clang_typestr)) > 0:
                typestr = clang_typestr

        self.meta.externs[name] = ExternInfo(
            typestr=typestr,
            type_name=self.get_typename(node_type),
            availability=self._get_availability(node),
        )

        if self.verbose:
            print(f"Added extern: {name} typestr {typestr.decode()!r}")

    def add_function(self, node: Cursor) -> None:
        name = node.spelling
        if name.startswith("__"):
            return

        funcspec = node.get_function_specifiers()

        # get return type
        return_type = node.result_type
        return_type_typestr = self.__get_typestr(return_type)[0]
        assert return_type_typestr is not None

        func = FunctionInfo(
            retval=ReturnInfo(typestr=return_type_typestr),
            args=[],
            availability=self._get_availability(node),
        )

        # Does it follow the CF returns-retained pattern?
        if any(x in name for x in ("Create", "Copy")):
            parts = name.split("Create", 1)
            second_part = parts[-1]
            first_letter = None if len(second_part) == 0 else second_part[0]
            if not first_letter or first_letter.isupper():
                func = replace(
                    func, retval=replace(func.retval, already_cfretained=True)
                )

        # is function inline?
        if funcspec is not None and any(
            x in funcspec for x in ("inline", "__inline__")
        ):
            func = replace(func, inline=True)

        # get arguments
        arg_gen = node.get_arguments()
        args = [] if arg_gen is None else list(arg_gen)

        assert node.type is not None
        if node.type.is_function_variadic():
            func = replace(func, variadic=True)

        for arg in args:
            arg_type = arg.type
            assert arg_type is not None
            typestr = self.__get_typestr(arg_type)[0]
            assert typestr is not None

            arginfo = ArgInfo(name=arg.spelling, typestr=typestr)

            if arg_type.kind == TypeKind.BLOCKPOINTER:
                arginfo = replace(arginfo, callable=self.__extract_block(arg_type))

            if arg_type.looks_like_function:
                arginfo = replace(arginfo, callable=self.__extract_function(arg_type))

            # get qualifiers
            qualifiers = arg.objc_decl_qualifiers or ObjCDeclQualifier(0)

            # update for special cases
            if FrameworkParser.__node_is_cferror_ptr(arg_type):
                qualifiers |= ObjCDeclQualifier.OUT
                arginfo = replace(arginfo, null_accepted=True, already_cfretained=True)

            # write back qualifiers
            encode_str = qualifiers.to_encode_string()
            if encode_str != "":
                arginfo = replace(arginfo, type_modifier=encode_str)

            # add the arginfo to the args array
            func.args.append(arginfo)

        # special handling for CF GetTypeID functions
        if name.endswith("GetTypeID"):
            assert return_type.spelling == "CFTypeID"
            arg_type_name = name[:-9] + "Ref"
            if arg_type_name in self.meta.cftypes:
                self.meta.cftypes[arg_type_name] = replace(
                    self.meta.cftypes[arg_type_name], gettypeid_func=name
                )

        # Ideally libclang would give us full parsing of __attributes__
        # but it doesn't. We do the best we can, nd what libclang gives us
        # is arguably better than is_variadic and arg_name == "format"
        index_of_printf_format_arg = FrameworkParser.__index_of_printf_format_arg(node)
        if index_of_printf_format_arg is not None:
            if index_of_printf_format_arg < len(func.args):
                arg_info = func.args[index_of_printf_format_arg]
                arg_info = replace(arg_info, printf_format=True)
                func.args[index_of_printf_format_arg] = arg_info
            else:
                # basically, libclang's C API doesnt expose the values
                # we need to be sure about this in all cases
                if func.variadic:
                    for idx, arg_info in enumerate(func.args):
                        if arg_info.name == "format":
                            arg_info = replace(arg_info, printf_format=True)
                            func.args[idx] = arg_info
        else:
            # Let's check the old way just in case. There *are* functions
            # in Apple's frameworks that don't have
            # the right __attribute__, believe it or not.
            if func.variadic:
                for idx, arg_info in enumerate(func.args):
                    if arg_info.name == "format":
                        diagnostic_string = (
                            "Looks like we might have a printf function, but we didn't"
                            + "find the attribute; take a look: "
                            + node.get_raw_contents()
                            + str(node.location)
                        )
                        print(diagnostic_string)
                        arg_info = replace(arg_info, printf_format=True)
                        func.args[idx] = arg_info

        self.meta.functions[name] = func

        # Log message
        if self.verbose:
            print(f"Added function {name}")

    def add_protocol(self, node: Cursor) -> None:
        protocol: ProtocolInfo

        self.meta.formal_protocols[node.spelling] = protocol = ProtocolInfo(
            implements=[
                x.referenced.spelling for x in node.get_adopted_protocol_nodes()
            ],
            methods=[],
            properties=[],
            availability=self._get_availability(node),
        )

        for decl in node.get_children() or []:
            if (
                decl.kind == CursorKind.OBJC_INSTANCE_METHOD_DECL
                or decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL
            ):
                meth = self.__extract_methoddecl(decl)
                meth = replace(meth, required=not decl.is_optional_for_protocol)
                protocol.methods.append(meth)

            elif decl.kind == CursorKind.OBJC_PROPERTY_DECL:
                assert decl.type is not None
                typestr, special = self.__get_typestr(decl.type)
                assert typestr is not None

                prop_attr = decl.get_property_attributes()
                getter = setter = None
                attributes: typing.Set[str] = util.sorted_set()
                for item in prop_attr or ():
                    if isinstance(item, str):
                        attributes.add(item)
                    elif item[0] == "getter":
                        getter = item[1]
                    elif item[0] == "setter":
                        setter = item[1]
                    else:
                        raise RuntimeError(f"Unexpected Attribute {attributes}")

                this_property = PropertyInfo(
                    name=decl.spelling,
                    typestr=typestr,
                    typestr_special=special,
                    getter=getter,
                    setter=setter,
                    attributes=attributes,
                    availability=self._get_availability(node),
                )
                protocol.properties.append(this_property)
            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

        if self.verbose:
            print(f"Added protocol: {node.spelling}")

    def add_informal_protocol(self, node: Cursor) -> None:
        self.meta.informal_protocols[node.spelling] = protocol = ProtocolInfo(
            implements=[
                x.referenced.spelling for x in node.get_adopted_protocol_nodes()
            ],
            methods=[],
            properties=[],
            availability=self._get_availability(node),
        )

        for decl in node.get_children() or []:
            if (
                decl.kind == CursorKind.OBJC_INSTANCE_METHOD_DECL
                or decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL
            ):
                protocol.methods.append(self.__extract_methoddecl(decl))

            elif decl.kind == CursorKind.OBJC_PROPERTY_DECL:
                assert decl.type is not None
                typestr, special = self.__get_typestr(decl.type)
                assert typestr is not None
                prop_attr = decl.get_property_attributes()
                getter = setter = None
                attributes: typing.Set[str] = util.sorted_set()
                for item in prop_attr or ():
                    if isinstance(item, str):
                        attributes.add(item)
                    elif item[0] == "getter":
                        getter = item[1]
                    elif item[0] == "setter":
                        setter = item[1]
                    else:
                        raise RuntimeError(f"Unexpected Attribute {attributes}")
                protocol.properties.append(
                    PropertyInfo(
                        name=decl.spelling,
                        typestr=typestr,
                        typestr_special=special,
                        getter=getter,
                        setter=setter,
                        attributes=attributes,
                        availability=self._get_availability(node),
                    )
                )
            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

        if self.verbose:
            print(f"Added informal protocol: {node.spelling}")

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

        meta = AvailabilityInfo()

        if availability["always_unavailable"]:
            suggestion = availability["unavailable_message"]
            meta = replace(
                meta,
                unavailable=True,
                suggestion=(suggestion if suggestion else "not available"),
            )

        if availability["always_deprecated"]:
            # Deprecated without version number, assume this is deprecated starting
            # at the first darwin-based macOS (10.0)
            meta = replace(meta, deprecated=10_00)

        try:
            platform = availability["platform"]["macos"]

        except KeyError:
            pass

        else:
            if platform["introduced"] is not None:
                meta = replace(meta, introduced=encode_version(platform["introduced"]))
            if platform["deprecated"] is not None:
                meta = replace(meta, deprecated=encode_version(platform["deprecated"]))
                if platform["message"]:
                    meta = replace(meta, deprecated_message=platform["message"])

            if platform["unavailable"]:
                meta = replace(
                    meta,
                    unavailable=True,
                    suggestion=(
                        platform["message"] if platform["message"] else "not available"
                    ),
                )

        return meta

    def add_class(self, node: Cursor):
        if node.spelling in self.meta.classes:
            class_info = self.meta.classes[node.spelling]
        else:
            # get superclass
            superclass_name = None
            for child in filter(
                lambda x: x.kind == CursorKind.OBJC_SUPER_CLASS_REF, node.get_children()
            ):
                superclass = child.referenced
                superclass_name = superclass.spelling

            self.meta.classes[node.spelling] = class_info = ClassInfo(
                super=superclass_name,
                implements=[],
                methods=[],
                categories=util.sorted_set(),
                properties=[],
                availability=self._get_availability(node),
            )

        for proto_ref in node.get_adopted_protocol_nodes():
            proto_str = proto_ref.referenced.spelling
            if proto_str not in class_info.implements:
                class_info.implements.append(proto_str)

        # This used to track visibility for methods, which is not a thing
        # (only for ivars) and made no sense

        for decl in node.get_children() or []:
            if (
                decl.kind == CursorKind.OBJC_INSTANCE_METHOD_DECL
                or decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL
            ):
                meth = self.__extract_methoddecl(decl)
                class_info.methods.append(meth)

            elif decl.kind == CursorKind.OBJC_PROPERTY_DECL:
                assert decl.type is not None
                typestr, special = self.__get_typestr(decl.type)
                assert typestr is not None

                prop_attr = decl.get_property_attributes()
                getter = setter = None
                attributes: typing.Set[str] = util.sorted_set()
                for item in prop_attr or ():
                    if isinstance(item, str):
                        attributes.add(item)
                    elif item[0] == "getter":
                        getter = item[1]
                    elif item[0] == "setter":
                        setter = item[1]
                    else:
                        raise RuntimeError(f"Unexpected Attribute {attributes}")

                class_info.properties.append(
                    PropertyInfo(
                        name=decl.spelling,
                        typestr=typestr,
                        typestr_special=special,
                        getter=getter,
                        setter=setter,
                        attributes=attributes,
                        availability=self._get_availability(node),
                    )
                )

            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

        if self.verbose:
            print(f"Added class: {node.spelling}")

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

        if class_name in self.meta.classes:
            class_info = self.meta.classes[class_name]
        else:
            self.meta.classes[class_name] = class_info = ClassInfo(
                categories=util.sorted_set(),
                methods=[],
                implements=[],
                properties=[],
                super=None,
            )

        if category_name not in class_info.categories:
            class_info.categories.add(category_name)

        for proto_ref in node.get_adopted_protocol_nodes():
            proto_str = proto_ref.referenced.spelling
            if proto_str not in class_info.implements:
                class_info.implements.append(proto_str)

        for decl in node.get_children() or []:
            if (
                decl.kind == CursorKind.OBJC_INSTANCE_METHOD_DECL
                or decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL
            ):
                meth = self.__extract_methoddecl(decl)
                meth = replace(meth, category=category_name)
                class_info.methods.append(meth)

            elif decl.kind == CursorKind.OBJC_PROPERTY_DECL:
                assert decl.type is not None
                typestr, special = self.__get_typestr(decl.type)
                assert typestr is not None

                prop_attr = decl.get_property_attributes()
                getter = setter = None
                attributes: typing.Set[str] = util.sorted_set()
                for item in prop_attr or ():
                    if isinstance(item, str):
                        attributes.add(item)
                    elif item[0] == "getter":
                        getter = item[1]
                    elif item[0] == "setter":
                        setter = item[1]
                    else:
                        raise RuntimeError(f"Unexpected Attribute {attributes}")

                class_info.properties.append(
                    PropertyInfo(
                        name=decl.spelling,
                        typestr=typestr,
                        typestr_special=special,
                        getter=getter,
                        setter=setter,
                        attributes=attributes,
                        category=category_name,
                        availability=self._get_availability(decl),
                    )
                )

            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

        if self.verbose:
            print(f"Added category on class: {class_name} named: {category_name}")

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
                    print("IGNORE MACRO", repr(key), repr(value))
                    continue

                value = value.strip()

                if value in ("static inline",):
                    continue

                if not value:
                    # Ignore empty macros
                    continue

                m = INT_RE.match(value)
                if m is not None:
                    self.meta.literals[key] = LiteralInfo(value=int(m.group(1), 0))
                    if self.verbose:
                        print(f"Added macro literal name: {key}")
                    continue

                m = FLOAT_RE.match(value)
                if m is not None:
                    self.meta.literals[key] = LiteralInfo(value=float(m.group(1)))
                    if self.verbose:
                        print(f"Added macro literal name: {key}")
                    continue

                m = STR_RE.match(value)
                if m is not None:
                    self.meta.literals[key] = LiteralInfo(
                        unicode=False, value=m.group(1)
                    )
                    if self.verbose:
                        print(f"Added macro literal name: {key}")
                    continue

                m = UNICODE_RE.match(value)
                if m is not None:
                    self.meta.literals[key] = LiteralInfo(
                        unicode=True, value=m.group(1)
                    )
                    if self.verbose:
                        print(f"Added macro literal name: {key}")
                    continue

                m = UNICODE2_RE.match(value)
                if m is not None:
                    self.meta.literals[key] = LiteralInfo(
                        value=m.group(1), unicode=True
                    )
                    if self.verbose:
                        print(f"Added macro literal name: {key}")
                    continue

                if value in ("nil", "NULL", "Nil"):
                    self.meta.literals[key] = LiteralInfo(value=None)
                    if self.verbose:
                        print(f"Added macro literal name: {key}")
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
                        self.meta.aliases[key] = AliasInfo(alias=m.group(1))
                        if self.verbose:
                            print(f"Added alias name: {key}")
                    continue

                if value == "(INT_MAX-1)":
                    self.meta.literals[key] = LiteralInfo(value=(1 << 31) - 1)
                    if self.verbose:
                        print(f"Added enum_values name: {key}")
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
                    self.meta.expressions[key] = ExpressionInfo(
                        expression=value.replace("NULL", "None")
                    )
                    if self.verbose:
                        print(f"Added expression name: {key}")
                    continue

                m = ERR_SUB_DEFINE_RE.match(value)
                if m is not None:
                    v = FrameworkParser.__parse_int(m.group(1))
                    self.meta.literals[key] = LiteralInfo(value=(v & 0xFFF) << 14)
                    if self.verbose:
                        print(f"Added enum_values name: {key}")
                    continue

                m = ERR_SYSTEM_DEFINE_RE.match(value)
                if m is not None:
                    v = FrameworkParser.__parse_int(m.group(1))
                    self.meta.literals[key] = LiteralInfo(value=(v & 0x3F) << 26)
                    if self.verbose:
                        print(f"Added enum_values name: {key}")
                    continue

                m = NULL_VALUE.match(value)
                if m is not None:
                    # For #define's like this:
                    #     #define kDSpEveryContext ((DSpContextReference)NULL)
                    self.meta.literals[key] = LiteralInfo(value=None)
                    if self.verbose:
                        print(f"Added literals name: {key}")

                    continue

                m = SC_SCHEMA_RE.match(value)
                if m is not None:
                    # noinspection PyProtectedMember
                    self.meta.externs[key] = ExternInfo(typestr=objc._C_ID)
                    if self.verbose:
                        print(f"Added externs name: {key}")
                    continue

                m = OR_EXPR_RE.match(value)
                if m is not None:
                    self.meta.expressions[key] = ExpressionInfo(expression=value)
                    if self.verbose:
                        print("Added expressions name: {key}")
                    continue

                m = CALL_VALUE.match(value)
                if m is not None:
                    self.meta.expressions[key] = ExpressionInfo(expression=value)
                    if self.verbose:
                        print(f"Added expressions name: {key}")
                    continue

                print(f"Warning: ignore #define {key!r} {value!r}")

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
                    self.meta.func_macros[key] = FunctionMacroInfo(definition=funcdef)
                    if self.verbose:
                        print(f"Added func_macros name: {key}")

    @staticmethod
    def __node_is_cferror_ptr(node: Type) -> bool:
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

    def __extract_function_like_thing(self, thing: Type) -> CallbackInfo:
        """

        @rtype : dict
        """
        if isinstance(thing, Type):
            if thing.kind == TypeKind.ATTRIBUTED:
                thing = thing.modified_type
            if thing.kind == TypeKind.TYPEDEF:
                assert thing.declaration is not None
                thing = thing.declaration.underlying_typedef_type.get_pointee()
        return_type = thing.get_result()
        assert return_type.kind != TypeKind.INVALID

        typestr = self.__get_typestr(return_type)[0]
        assert typestr is not None

        return_info = CallbackArgInfo(typestr=typestr)

        if return_type.nullability == NullabilityKind.NONNULL:
            return_info = replace(return_info, null_accepted=False)
        elif return_type.nullability == NullabilityKind.NULLABLE:
            return_info = replace(return_info, null_accepted=True)

        result = CallbackInfo(retval=return_info, args=[])

        arg_types_iter = thing.argument_types()
        arg_types = [] if arg_types_iter is None else list(arg_types_iter)

        if thing.is_function_variadic():
            result = replace(result, variadic=True)

        for arg in arg_types:
            assert arg.kind != TypeKind.INVALID
            typestr = self.__get_typestr(arg)[0]
            assert typestr is not None

            arginfo = CallbackArgInfo(typestr=typestr)

            if arg.nullability == NullabilityKind.NONNULL:
                arginfo = replace(arginfo, null_accepted=False)
            elif arg.nullability == NullabilityKind.NULLABLE:
                arginfo = replace(arginfo, null_accepted=True)

            if arg.kind == TypeKind.BLOCKPOINTER:
                arginfo = replace(arginfo, calblack=self.__extract_block2(arg.type))

            if arg.looks_like_function:
                arginfo = replace(arginfo, calblack=self.__extract_function2(arg.type))

            result.args.append(arginfo)

        return result

    def __extract_block(self, block: typing.Union[Type, Cursor]) -> CallbackInfo:
        """

        @rtype : dict
        """
        if isinstance(block, Cursor):
            assert block.type is not None
            block = block.type

        assert isinstance(block, Type)

        if isinstance(block, Type) and block.kind == TypeKind.BLOCKPOINTER:
            block = block.get_pointee()

        assert isinstance(block, Type)
        return self.__extract_function_like_thing(block)

    # The three "...2" methods below are functionally identical except
    # for (a) type annotations and (b) not suppporting callbacks themselfes.
    #

    def __extract_function_like_thing2(self, thing: Type) -> CallbackInfo2:
        """

        @rtype : dict
        """
        return_type = thing.get_result()
        assert return_type.kind != TypeKind.INVALID

        typestr = self.__get_typestr(return_type)[0]
        assert typestr is not None

        return_info = CallbackArgInfo2(typestr=typestr)

        if return_type.nullability == NullabilityKind.NONNULL:
            return_info = replace(return_info, null_accepted=False)
        elif return_type.nullability == NullabilityKind.NULLABLE:
            return_info = replace(return_info, null_accepted=True)

        result = CallbackInfo2(retval=return_info, args=[])

        arg_types_iter = thing.argument_types()
        arg_types = [] if arg_types_iter is None else list(arg_types_iter)

        if thing.is_function_variadic():
            result = replace(result, variadic=True)

        for arg in arg_types:
            assert arg.kind != TypeKind.INVALID
            typestr = self.__get_typestr(arg)[0]
            assert typestr is not None

            arginfo = CallbackArgInfo2(typestr=typestr)

            if arg.nullability == NullabilityKind.NONNULL:
                arginfo = replace(arginfo, null_accepted=False)
            elif arg.nullability == NullabilityKind.NULLABLE:
                arginfo = replace(arginfo, null_accepted=True)

            if arg.kind == TypeKind.BLOCKPOINTER:
                print("2nd level nested blocks not supported")
                # arginfo["callable"] = self.__extract_block2(arg.type)

            if arg.looks_like_function:
                print("2nd level nested callbacks not supported")
                # arginfo["callable"] = self.__extract_function2(arg_type)

            result.args.append(arginfo)

        return result

    def __extract_block2(self, block: typing.Union[Type, Cursor]) -> CallbackInfo2:
        """

        @rtype : dict
        """
        if isinstance(block, Cursor):
            assert block.type is not None
            block = block.type

        assert isinstance(block, Type)

        if isinstance(block, Type) and block.kind == TypeKind.BLOCKPOINTER:
            block = block.get_pointee()

        assert isinstance(block, Type)
        return self.__extract_function_like_thing2(block)

    def __extract_function2(self, func: typing.Union[Type, Cursor]) -> CallbackInfo2:
        """

        @rtype : dict
        """
        if isinstance(func, Cursor):
            assert func.type is not None
            func = func.type

        assert isinstance(func, Type)

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

        assert isinstance(func, Type)
        return self.__extract_function_like_thing2(func)

    def __extract_function(self, func: typing.Union[Type, Cursor]) -> CallbackInfo:
        """

        @rtype : dict
        """
        if isinstance(func, Cursor):
            assert func.type is not None
            func = func.type

        assert isinstance(func, Type)

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

        assert isinstance(func, Type)
        return self.__extract_function_like_thing(func)

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

        type_name = self.get_typename(decl.result_type)
        retval = ReturnInfo(
            typestr=typestr, typestr_special=special, type_name=type_name
        )

        if decl.result_type.nullability == NullabilityKind.NONNULL:
            retval = replace(retval, null_accepted=False)
        elif decl.result_type.nullability == NullabilityKind.NULLABLE:
            retval = replace(retval, null_accepted=True)

        meth = MethodInfo(
            selector=decl.spelling,
            class_method=bool(decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL),
            retval=retval,
            args=[],
            availability=self._get_availability(decl),
            variadic=decl.is_variadic,
        )

        for a in decl.get_arguments():
            if a is not None:
                assert a.type is not None
                typestr, special = self.__get_typestr(a.type)
                assert typestr is not None

                type_name = self.get_typename(a.type)
            else:
                typestr, special = objc._C_ID, False
                type_name = None

            arg = ArgInfo(
                typestr=typestr,
                typestr_special=special,
                name=a.spelling,
                type_name=type_name,
            )

            if a is not None:
                if a.type.nullability == NullabilityKind.NONNULL:
                    arg = replace(arg, null_accepted=False)
                elif a.type.nullability == NullabilityKind.NULLABLE:
                    arg = replace(arg, null_accepted=True)

            meth.args.append(arg)

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
            assert isinstance(obj, Type)
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
        self,
        obj: typing.Union[Type, Cursor],
        exogenous_name: typing.Optional[str] = None,
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
                assert isinstance(obj, Cursor)
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
            seen = util.sorted_set()

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
                    # NOTE: For all current platforms (x86_64 and arm64
                    #       sizeof(BOOL) == sizoef(bool), just using
                    #       _C_BOOL should work (and is the default
                    #       encoding for ARM64)
                    assert canonical_type.kind in (TypeKind.SCHAR, TypeKind.BOOL)
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
            seen = util.sorted_set()

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
                assert clang_type is not None
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
                            assert c_type is not None
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

    def __init__(self, parser: FrameworkParser):
        if parser is None:
            raise ValueError(parser)

        self._parser = parser
        # Record all records seen in header files, even
        # if they are not in a framework we're scanning.
        # This way we can emit the expected metadata
        # for "typedef CGRect NSRect" (as used in the Foundation
        # headers on x86_64)
        self.__all_structs: typing.Dict[str, Type] = {}

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
        assert node.type is not None
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
        assert typedef_type is not None
        assert typedef_type.declaration is not None
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
        # self._parser.add_typedef(typedef_name, underlying_name)

        # Ignore typedefs that resolve to function declarations
        if (
            underlying_type.declaration is not None
            and underlying_type.declaration.kind == CursorKind.FUNCTION_DECL
        ):
            return

        if underlying_name in self._parser.meta.cftypes:
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
