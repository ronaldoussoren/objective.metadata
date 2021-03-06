from setuptools import setup

setup(
    name="objective.metadata",
    description="PyObjC Metadata generator",
    long_description="""
        objective.metadata is a metadata generator for PyObjC

        This package provides tools for extracting definitions from
        Objective-C header files that cannot be extracted from the
        Objective-C runtime.
    """,
    install_requires=["pyobjc-core", "macholib>=1.4.3", "dataclasses-json"],
    license="MIT",
    version="0.1",
    author="Ronald Oussoren",
    author_email="ronald.oussoren@mac.com",
    url="https://github.com/ronaldoussoren/objective.metadata",
    platforms="MacOSX",
    namespace_packages=["objective"],
    packages=["objective", "objective.metadata"],
    entry_points={
        "console_scripts": [
            "objective-metadata-tool                 = objective.metadata.main:main"
        ]
    },
)
