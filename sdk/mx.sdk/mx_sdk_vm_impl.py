#
# Copyright (c) 2019, Oracle and/or its affiliates. All rights reserved.
# DO NOT ALTER OR REMOVE COPYRIGHT NOTICES OR THIS FILE HEADER.
#
# The Universal Permissive License (UPL), Version 1.0
#
# Subject to the condition set forth below, permission is hereby granted to any
# person obtaining a copy of this software, associated documentation and/or
# data (collectively the "Software"), free of charge and under any and all
# copyright rights in the Software, and any and all patent rights owned or
# freely licensable by each licensor hereunder covering either (i) the
# unmodified Software as contributed to or provided by such licensor, or (ii)
# the Larger Works (as defined below), to deal in both
#
# (a) the Software, and
#
# (b) any piece of software and/or hardware listed in the lrgrwrks.txt file if
# one is included with the Software each a "Larger Work" to which the Software
# is contributed by such licensors),
#
# without restriction, including without limitation the rights to copy, create
# derivative works of, display, perform, and distribute the Software and make,
# use, sell, offer for sale, import, export, have made, and have sold the
# Software and the Larger Work(s), and to sublicense the foregoing rights on
# either these or other terms.
#
# This license is subject to the following condition:
#
# The above copyright notice and either this complete permission notice or at a
# minimum a reference to the UPL must be included in all copies or substantial
# portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#

from __future__ import print_function

from abc import ABCMeta
from argparse import ArgumentParser
import glob
import io
import json
import os
from os.path import relpath, join, dirname, basename, exists, isfile, normpath, abspath, isdir
import pprint
import re
import subprocess

import mx
import mx_gate
import mx_subst
import mx_sdk
import mx_sdk_vm


import sys

if sys.version_info[0] < 3:
    _unicode = unicode # pylint: disable=undefined-variable
    def _decode(x):
        return x
else:
    _unicode = str
    def _decode(x):
        return x.decode()

def unicode_utf8(string):
    if sys.version_info[0] < 3:
        if isinstance(string, str):
            return unicode(string, 'utf-8')
    elif isinstance(string, bytes):
        return str(string)
    return string


def _with_metaclass(meta, *bases):
    """Create a base class with a metaclass."""

    # Copyright (c) 2010-2018 Benjamin Peterson
    # Taken from six, Python compatibility library
    # MIT license

    # This requires a bit of explanation: the basic idea is to make a dummy
    # metaclass for one level of class instantiation that replaces itself with
    # the actual metaclass.
    class MetaClass(type):

        def __new__(mcs, name, this_bases, d):
            return meta(name, bases, d)

        @classmethod
        def __prepare__(mcs, name, this_bases):
            return meta.__prepare__(name, bases)
    return type.__new__(MetaClass, '_with_metaclass({}, {})'.format(meta, bases), (), {}) #pylint: disable=unused-variable


_suite = mx.suite('sdk')
""":type: mx.SourceSuite | mx.Suite"""

_exe_suffix = mx.exe_suffix('')
""":type: str"""
_lib_suffix = mx.add_lib_suffix("")
_lib_prefix = mx.add_lib_prefix("")


_graalvm_base_name = 'GraalVM'
_graalvm_maven_attributes = {'groupId': 'org.graalvm'}


default_components = []

graalvm_version_regex = re.compile(r'.*\n.*\n[0-9a-zA-Z()\- ]+GraalVM[a-zA-Z_ ]+(?P<graalvm_version>[0-9a-z_\-.+]+) \(build [0-9a-z\-.+]+, mixed mode\)')

_registered_graalvm_components = {}
_env_tests = []


def gate_body(args, tasks):
    with mx_gate.Task('Sdk: GraalVM dist names', tasks, tags=['names']) as t:
        if t:
            for dist_name, _, _, suite, env_file in mx_sdk_vm._vm_configs:
                if env_file is not False:
                    _env_file = env_file or dist_name
                    graalvm_dist_name = '{base_name}_{dist_name}_JAVA{jdk_version}'.format(base_name=_graalvm_base_name, dist_name=dist_name, jdk_version=_src_jdk_version).upper().replace('-', '_')
                    mx.log("Checking that the env file '{}' in suite '{}' produces a GraalVM distribution named '{}'".format(_env_file, suite.name, graalvm_dist_name))
                    out = mx.LinesOutputCapture()
                    retcode = mx.run_mx(['--no-warning', '--env', _env_file, 'graalvm-dist-name'], suite, out=out, err=out, env={}, nonZeroIsFatal=False)
                    if retcode != 0:
                        mx.abort("Unexpected return code '{}' for 'graalvm-dist-name' for env file '{}' in suite '{}'. Output: \n{}".format(retcode, _env_file, suite.name, '\n'.join(out.lines)))
                    if len(out.lines) != 1 or out.lines[0] != graalvm_dist_name:
                        mx.abort("Unexpected GraalVM dist name for env file '{}' in suite '{}'.\nExpected: '{}', actual: '{}'.\nDid you forget to update the registration of the GraalVM config?".format(_env_file, suite.name, graalvm_dist_name, '\n'.join(out.lines)))


mx_gate.add_gate_runner(_suite, gate_body)


def registered_graalvm_components(stage1=False):
    if stage1 not in _registered_graalvm_components:
        components_include_list = _components_include_list()
        if components_include_list is None:
            components_include_list = mx_sdk.graalvm_components()

        excluded = _excluded_components()
        components_to_build = []

        def is_excluded(component):
            return component.name in excluded or component.short_name in excluded

        def add_dependencies(dependencies, excludes=True):
            components = dependencies[:]
            while components:
                component = components.pop(0)
                if component not in components_to_build and not (excludes and is_excluded(component)):
                    components_to_build.append(component)
                    components.extend(component.direct_dependencies())

        # Expand dependencies
        add_dependencies([mx_sdk.graalvm_component_by_name(name) for name in default_components], excludes=True)
        add_dependencies(components_include_list, excludes=True)

        # If we are going to build native launchers or libraries, i.e., if SubstrateVM is included,
        # we need native-image in stage1 to build them, even if the Native Image component is excluded.
        if stage1:
            if any(component.short_name == 'svmee' for component in components_to_build):
                add_dependencies([mx_sdk.graalvm_component_by_name('niee')], excludes=False)
            elif any(component.short_name == 'svm' for component in components_to_build):
                add_dependencies([mx_sdk.graalvm_component_by_name('ni')], excludes=False)

        if not any(component.short_name == 'svm' for component in components_to_build):
            # SVM is not included, remove GraalVMSvmMacros
            components_to_build = [component for component in components_to_build if not isinstance(component, mx_sdk.GraalVMSvmMacro)]

        mx.logv('Components: {}'.format([c.name for c in components_to_build]))
        _registered_graalvm_components[stage1] = components_to_build
    return _registered_graalvm_components[stage1]


def _get_component_type_base(c, apply_substitutions=False):
    if isinstance(c, mx_sdk.GraalVmLanguage):
        result = '<jre_base>/languages/'
    elif isinstance(c, mx_sdk.GraalVmTool):
        result = '<jre_base>/tools/'
    elif isinstance(c, mx_sdk.GraalVmJdkComponent):
        result = '<jdk_base>/lib/'
    elif isinstance(c, mx_sdk.GraalVmJreComponent):
        result = '<jre_base>/lib/'
    elif isinstance(c, mx_sdk.GraalVMSvmMacro):
        svm_component = get_component('svm', stage1=True)
        result = _get_component_type_base(svm_component, apply_substitutions=apply_substitutions) + '/' + svm_component.dir_name + '/macros/'
    elif isinstance(c, mx_sdk.GraalVmComponent):
        result = '<jdk_base>/'
    else:
        raise mx.abort("Unknown component type for {}: {}".format(c.name, type(c).__name__))
    if apply_substitutions:
        result = get_final_graalvm_distribution().path_substitutions.substitute(result)
    return result

def _get_jdk_base(jdk):
    jdk_dir = jdk.home
    if jdk_dir.endswith(os.path.sep):
        jdk_dir = jdk_dir[:-len(os.path.sep)]
    if jdk_dir.endswith('/Contents/Home'):
        jdk_base = 'Contents/Home'
        jdk_dir = jdk_dir[:-len('/Contents/Home')]
    else:
        jdk_base = '.'
    return jdk_dir, jdk_base

_src_jdk = mx_sdk_vm.base_jdk()
_src_jdk_version = mx_sdk_vm.base_jdk_version()

# Example:
#   macOS:
#     _src_jdk_dir  = /Library/Java/JavaVirtualMachines/oraclejdk1.8.0_221-jvmci-19.2-b02
#     _src_jdk_base = Contents/Home
#   Others:
#     _src_jdk_dir  = $JAVA_HOME (e.g. /usr/lib/jvm/oraclejdk1.8.0_212-jvmci-19.2-b01)
#     _src_jdk_base = .
_src_jdk_dir, _src_jdk_base = _get_jdk_base(_src_jdk)

class BaseGraalVmLayoutDistribution(_with_metaclass(ABCMeta, mx.LayoutDistribution)):
    def __init__(self, suite, name, deps, components, is_graalvm, exclLibs, platformDependent, theLicense, testDistribution,
                 add_jdk_base=False,
                 base_dir=None,
                 path=None,
                 with_polyglot_launcher=False,
                 with_lib_polyglot=False,
                 stage1=False,
                 **kw_args): # pylint: disable=super-init-not-called
        self.components = components
        layout = {}
        src_jdk_base = _src_jdk_base if add_jdk_base else '.'
        assert src_jdk_base
        base_dir = base_dir or '.'

        if base_dir != '.':
            self.jdk_base = '/'.join([base_dir, src_jdk_base]) if src_jdk_base != '.' else base_dir
        else:
            self.jdk_base = src_jdk_base

        if _src_jdk_version == 8:
            self.jre_base = '/'.join((self.jdk_base, 'jre'))
        else:
            self.jre_base = self.jdk_base

        path_substitutions = mx_subst.SubstitutionEngine(mx_subst.path_substitutions)
        path_substitutions.register_no_arg('jdk_base', lambda: self.jdk_base)
        path_substitutions.register_no_arg('jre_base', lambda: self.jre_base)

        string_substitutions = mx_subst.SubstitutionEngine(mx_subst.string_substitutions)
        string_substitutions.register_no_arg('version', _suite.release_version)
        string_substitutions.register_no_arg('graalvm_os', get_graalvm_os())

        _layout_provenance = {}

        def _add(_layout, dest, src, component=None, with_sources=False):
            """
            :type _layout: dict[str, list[str] | str]
            :type dest: str
            :type src: list[str | dict] | str | dict
            """
            assert dest.startswith('<jdk_base>') or dest.startswith('<jre_base>') or base_dir == '.' or dest.startswith(base_dir), dest
            src = src if isinstance(src, list) else [src]
            if not src:
                return

            if not dest.endswith('/') and dest in _layout:
                if dest not in _layout_provenance or _layout_provenance[dest] is None:
                    mx.abort(
                        "Can not override '{dest}' which is part of the base GraalVM layout. ({component} tried to set {dest}<-{src})".format(
                            dest=dest, src=src, component=component.name if component else None))
                previous_component = _layout_provenance[dest]
                if not component:
                    mx.abort(
                        "Suspicious override in GraalVM layout: tried to set {}<-{} without a component while it already existed ({} set it to {})".format(
                            dest, src, previous_component.name, _layout[dest]))
                if component.priority <= previous_component.priority:
                    mx.logv("'Skipping '{}<-{}' from {c}' ({c}.priority={cp} <= {pc}.priority={pcp})".format(dest, src, c=component.name, pc=previous_component.name, cp=component.priority, pcp=previous_component.priority))
                    return
                else:
                    _layout[dest] = []

            mx.logvv("'Adding '{}: {}' to the layout'".format(dest, src))
            _layout_provenance[dest] = component
            if with_sources and _include_sources():
                for _src in list(src):
                    src_dict = mx.LayoutDistribution._as_source_dict(_src, name, dest)
                    if src_dict['source_type'] == 'dependency' and src_dict['path'] is None:
                        src_src_dict = {
                            'source_type': 'dependency',
                            'dependency': src_dict['dependency'],
                            'path': '*.src.zip',
                            'optional': True,
                            'if_stripped': 'exclude',
                        }
                        src.append(src_src_dict)
            _layout.setdefault(dest, []).extend(src)

        def _patch_darwin_jdk():
            """
            :rtype: list[(str, source_dict)], list[str]
            """
            orig_info_plist = join(_src_jdk_dir, 'Contents', 'Info.plist')
            if exists(orig_info_plist):
                from mx import etreeParse
                root = etreeParse(orig_info_plist)
                found_el = False
                for el in root.iter():
                    if el.tag == 'key' and el.text == 'CFBundleName':
                        found_el = True
                    elif found_el:
                        assert el.tag == 'string'
                        graalvm_bundle_name = '{} {}'.format(self.base_name, self.vm_config_name.upper()) if self.vm_config_name is not None else name.lower()
                        graalvm_bundle_name += ' ' + graalvm_version()
                        el.text = graalvm_bundle_name
                        bio = io.BytesIO()
                        root.write(bio) # When porting to Python 3, we can use root.write(StringIO(), encoding="unicode")
                        plist_src = {
                            'source_type': 'string',
                            'value': _decode(bio.getvalue()),
                            'ignore_value_subst': True
                        }
                        return [(base_dir + '/Contents/Info.plist', plist_src)], [orig_info_plist]
            return [], []

        svm_component = get_component('svm', stage1=True)

        def _add_native_image_macro(image_config, component=None):
            # Add the macros if SubstrateVM is included, as images could be created later with an installable Native Image
            if svm_component and (component is None or has_component(component.short_name, stage1=False)):
                # create macro to build this launcher
                _macro_dir = _get_component_type_base(svm_component) + svm_component.dir_name + '/macros/' + GraalVmNativeProperties.macro_name(image_config) + '/'
                _project_name = GraalVmNativeProperties.project_name(image_config)
                _add(layout, _macro_dir, 'dependency:{}'.format(_project_name), component)  # native-image.properties is the main output
                if isinstance(image_config, mx_sdk.LanguageLauncherConfig):
                    _add(layout, _macro_dir, 'dependency:{}'.format(_project_name) + '/polyglot.config', component)
                # Add profiles
                for profile in _image_profile(GraalVmNativeProperties.canonical_image_name(image_config)):
                    _add(layout, _macro_dir, 'file:{}'.format(abspath(profile)))

        def _add_link(_dest, _target, _component=None):
            assert _dest.endswith('/')
            _linkname = relpath(path_substitutions.substitute(_target), start=path_substitutions.substitute(_dest[:-1]))
            if _linkname != basename(_target):
                if mx.is_windows():
                    if _target.endswith('.exe') or _target.endswith('.cmd'):
                        link_template_name = join(_suite.mxDir, 'vm', 'exe_link_template.cmd')
                        with open(link_template_name, 'r') as template:
                            _template_subst = mx_subst.SubstitutionEngine(mx_subst.string_substitutions)
                            _template_subst.register_no_arg('target', normpath(_linkname))
                            contents = _template_subst.substitute(template.read())
                        full_dest = _dest + basename(_target)[:-len('.exe')] + '.cmd'
                        _add(layout, full_dest, 'string:{}'.format(contents), _component)
                        return full_dest
                    else:
                        mx.abort("Cannot create link on windows for {}->{}".format(_dest, _target))
                else:
                    _add(layout, _dest, 'link:{}'.format(_linkname), _component)
                    return _dest + basename(_target)

        if is_graalvm:
            if stage1:
                # 1. we do not want a GraalVM to be used as base-JDK
                # 2. we don't need to check if the base JDK is JVMCI-enabled, since JVMCIVersionCheck takes care of that when the GraalVM compiler is a registered component
                check_versions(_src_jdk, graalvm_version_regex=graalvm_version_regex, expect_graalvm=False, check_jvmci=False)

            # Add base JDK
            exclude_base = _src_jdk_dir
            exclusion_list = []
            if src_jdk_base != '.':
                exclude_base = join(exclude_base, src_jdk_base)
            if mx.get_os() == 'darwin':
                hsdis = '/jre/lib/' + mx.add_lib_suffix('hsdis-' + mx.get_arch())
                incl_list, excl_list = _patch_darwin_jdk()
                for d, s in incl_list:
                    _add(layout, d, s)
                exclusion_list += excl_list
            else:
                hsdis = '/jre/lib/' + mx.get_arch() + '/' + mx.add_lib_suffix('hsdis-' + mx.get_arch())
            if _src_jdk_version == 8:
                _add(layout, base_dir, {
                    'source_type': 'file',
                    'path': _src_jdk_dir,
                    'exclude': exclusion_list + [
                        exclude_base + '/COPYRIGHT',
                        exclude_base + '/LICENSE',
                        exclude_base + '/README.html',
                        exclude_base + '/THIRDPARTYLICENSEREADME.txt',
                        exclude_base + '/THIRDPARTYLICENSEREADME-JAVAFX.txt',
                        exclude_base + '/release',
                        exclude_base + '/bin/jvisualvm',
                        exclude_base + '/bin/jvisualvm.exe',
                        exclude_base + '/lib/visualvm',
                        exclude_base + hsdis,
                    ] + ([
                        exclude_base + '/bin/jmc',
                        exclude_base + '/lib/missioncontrol',
                    ] if mx.get_os() == 'darwin' else [])
                })
            else:
                # TODO(GR-8329): add exclusions
                _add(layout, self.jdk_base + '/', {
                    'source_type': 'dependency',
                    'dependency': 'graalvm-jimage',
                    'path': '*',
                })
                # Temporary workaround until GR-16855 an SVM module provides the .a files
                static_libs_path = _src_jdk_dir + (('/' + _src_jdk_base) if _src_jdk_base != '.' else '') + '/lib/*.a'
                if glob.glob(static_libs_path):
                    _add(layout, self.jdk_base + '/lib/', {
                        'source_type': 'file',
                        'path': static_libs_path,
                    })
                elif _get_svm_support().is_supported():
                    mx.abort("Cannot find static libraries in '{}', required by SubstrateVM on JDK9+.\nAre you building with a recent LabsJDK?".format(dirname(static_libs_path)))

            # Add vm.properties
            vm_name = graalvm_vm_name(self, _src_jdk)

            if mx.get_os() == 'windows':
                _add(layout, "<jre_base>/bin/server/vm.properties", "string:name=" + vm_name)
            elif mx.get_os() == 'darwin' or _src_jdk_version >= 9:
                # on macOS and jdk >= 9, the <arch> directory is not used
                _add(layout, "<jre_base>/lib/server/vm.properties", "string:name=" + vm_name)
            else:
                _add(layout, "<jre_base>/lib/<arch>/server/vm.properties", "string:name=" + vm_name)

            if _src_jdk_version == 8 and any(comp.jvmci_parent_jars for comp in registered_graalvm_components(stage1)):
                _add(layout, '<jre_base>/lib/jvmci/parentClassLoader.classpath', 'dependency:{}'.format(GraalVmJvmciParentClasspath.project_name()))

            # Add Polyglot launcher
            if with_polyglot_launcher:
                polyglot_launcher_project = get_polyglot_launcher_project()
                if not stage1:
                    _add(layout, "<jre_base>/bin/<exe:polyglot>", "dependency:" + polyglot_launcher_project.name)
                    if _src_jdk_version == 8:
                        _add_link("<jdk_base>/bin/", "<jre_base>/bin/" + mx.exe_suffix('polyglot'))
                _add_native_image_macro(polyglot_launcher_project.native_image_config)

            # Add libpolyglot library
            if with_lib_polyglot:
                lib_polyglot_project = get_lib_polyglot_project()
                # Note that `jre/lib/polyglot` is synchronized with `org.graalvm.polyglot.install_name_id` in `get_lib_polyglot_project`
                if not stage1:
                    source_type = 'skip' if _skip_libraries(lib_polyglot_project.native_image_config) else 'dependency'
                    libpolyglot_dest = "<jre_base>/lib/polyglot/" + lib_polyglot_project.native_image_name
                    _add(layout, libpolyglot_dest, source_type + ":" + lib_polyglot_project.name)
                    # TODO add directive to output .h files there in the macro
                    _add(layout, "<jre_base>/lib/polyglot/", source_type + ":" + lib_polyglot_project.name + "/*.h")
                _add_native_image_macro(lib_polyglot_project.native_image_config)

            # Add release file
            _sorted_suites = sorted(mx.suites(), key=lambda s: s.name)
            _metadata = self._get_metadata(_sorted_suites)
            _add(layout, "<jdk_base>/release", "string:{}".format(_metadata))

        # Add the rest of the GraalVM

        component_suites = {}
        has_graal_compiler = False
        for _component in self.components:
            mx.logv('Adding {} ({}) to the {} {}'.format(_component.name, _component.__class__.__name__, name, self.__class__.__name__))
            _component_type_base = _get_component_type_base(_component)
            if isinstance(_component, (mx_sdk.GraalVmJreComponent, mx_sdk.GraalVmJdkComponent)):
                if mx.get_os() == 'windows':
                    _component_jvmlib_base = _component_type_base[:-len('lib/')] + 'bin/'
                else:
                    _component_jvmlib_base = _component_type_base
            elif mx.get_os() == 'windows':
                _component_jvmlib_base = '<jre_base>/bin/'
            else:
                _component_jvmlib_base = '<jre_base>/lib/'
            if _src_jdk_version < 9 and mx.get_os() not in ['darwin', 'windows']:
                _jvm_library_dest = _component_jvmlib_base + mx.get_arch() + '/'
            else:
                _jvm_library_dest = _component_jvmlib_base

            if _component.dir_name:
                _component_base = _component_type_base + _component.dir_name + '/'
            else:
                _component_base = _component_type_base

            if _src_jdk_version == 8:
                _add(layout, '<jre_base>/lib/boot/', ['dependency:' + d for d in _component.boot_jars], _component, with_sources=True)
            _add(layout, _component_base, ['dependency:' + d for d in _component.jar_distributions + _component.jvmci_parent_jars], _component, with_sources=True)
            _add(layout, _component_base + 'builder/', ['dependency:' + d for d in _component.builder_jar_distributions], _component, with_sources=True)
            _add(layout, _component_base, [{
                'source_type': 'extracted-dependency',
                'dependency': d,
                'exclude': _component.license_files if mx.get_opts().no_licenses else [],
                'path': None,
            } for d in _component.support_distributions], _component)
            _add(layout, '<jdk_base>/include/', [{
                'source_type': 'extracted-dependency',
                'dependency': d,
                'exclude': [],
                'path': None,
            } for d in _component.support_headers_distributions], _component)
            _add(layout, _jvm_library_dest, [{
                'source_type': 'extracted-dependency',
                'dependency': d,
                'exclude': [],
                'path': None,
            } for d in _component.support_libraries_distributions], _component)
            if isinstance(_component, mx_sdk.GraalVmJvmciComponent) and _src_jdk_version == 8:
                _add(layout, '<jre_base>/lib/jvmci/', ['dependency:' + d for d in _component.jvmci_jars], _component, with_sources=True)

            if isinstance(_component, mx_sdk.GraalVmJdkComponent):
                _jdk_jre_bin = '<jdk_base>/bin/'
            else:
                _jdk_jre_bin = '<jre_base>/bin/'

            _licenses = _component.third_party_license_files
            if not mx.get_opts().no_licenses:
                _licenses = _licenses + _component.license_files
            for _license in _licenses:
                if mx.is_windows() or isinstance(self, mx.AbstractJARDistribution):
                    if _component_base == '<jdk_base>/':
                        pass  # already in place from the support dist
                    elif len(_component.support_distributions) == 1:
                        _support = _component.support_distributions[0]
                        _add(layout, '<jdk_base>/', 'extracted-dependency:{}/{}'.format(_support, _license), _component)
                    else:
                        mx.warn("Can not add license: " + _license)
                else:
                    _add_link('<jdk_base>/', _component_base + _license, _component)

            _jre_bin_names = []
            graalvm_dists = set()

            for _launcher_config in _get_launcher_configs(_component):
                graalvm_dists.update(_launcher_config.jar_distributions)
                _launcher_dest = _component_base + GraalVmLauncher.get_launcher_destination(_launcher_config, stage1)
                # add `LauncherConfig.destination` to the layout
                _add(layout, _launcher_dest, 'dependency:' + GraalVmLauncher.launcher_project_name(_launcher_config, stage1), _component)
                if _debug_images() and GraalVmLauncher.is_launcher_native(_launcher_config, stage1) and _get_svm_support().is_debug_supported():
                    _add(layout, dirname(_launcher_dest) + '/', 'dependency:' + GraalVmLauncher.launcher_project_name(_launcher_config, stage1) + '/*.debug', _component)
                    if _include_sources():
                        _add(layout, dirname(_launcher_dest) + '/', 'dependency:' + GraalVmLauncher.launcher_project_name(_launcher_config, stage1) + '/sources', _component)
                # add links from jre/bin to launcher
                if _launcher_config.default_symlinks:
                    _link_path = _add_link(_jdk_jre_bin, _launcher_dest, _component)
                    _jre_bin_names.append(basename(_link_path))
                for _component_link in _launcher_config.links:
                    _link_dest = _component_base + _component_link
                    # add links `LauncherConfig.links` -> `LauncherConfig.destination`
                    _add(layout, _link_dest, 'link:{}'.format(relpath(_launcher_dest, start=dirname(_link_dest))), _component)
                    # add links from jre/bin to component link
                    if _launcher_config.default_symlinks:
                        _link_path = _add_link(_jdk_jre_bin, _link_dest, _component)
                        _jre_bin_names.append(basename(_link_path))
                _add_native_image_macro(_launcher_config, _component)
            for _library_config in _get_library_configs(_component):
                graalvm_dists.update(_library_config.jar_distributions)
                if _library_config.jvm_library:
                    assert isinstance(_component, (mx_sdk.GraalVmJdkComponent, mx_sdk.GraalVmJreComponent))
                    _svm_library_dest = _jvm_library_dest
                else:
                    _svm_library_dest = _component_base
                _svm_library_dest += _library_config.destination
                if not stage1:
                    source_type = 'skip' if _skip_libraries(_library_config) else 'dependency'
                    # add `LibraryConfig.destination` to the layout
                    _add(layout, _svm_library_dest, source_type + ':' + GraalVmNativeImage.project_name(_library_config), _component)
                _add_native_image_macro(_library_config, _component)

            _add(layout, '<jre_base>/lib/graalvm/', ['dependency:' + d for d in graalvm_dists], _component, with_sources=True)

            for _provided_executable in _component.provided_executables:
                if _component.short_name == 'vvm':
                    _add(layout, _jdk_jre_bin, 'extracted-dependency:tools:VISUALVM_PLATFORM_SPECIFIC/./' + _provided_executable, _component)
                else:
                    _link_dest = _component_base + _provided_executable
                    _link_path = _add_link(_jdk_jre_bin, _link_dest, _component)
                    _jre_bin_names.append(basename(_link_path))

            if _src_jdk_version == 8 and 'jre' in _jdk_jre_bin:
                # Add jdk to jre links
                for _name in _jre_bin_names:
                    _add_link('<jdk_base>/bin/', '<jre_base>/bin/' + _name, _component)

            if isinstance(_component, mx_sdk.GraalVmJvmciComponent) and _component.graal_compiler:
                has_graal_compiler = True

            if isinstance(_component, mx_sdk.GraalVmLanguage) and not is_graalvm:
                # add language-specific release file
                component_suites.setdefault(_component_base, []).append(_component.suite)

        for _base, _suites in component_suites.items():
            _metadata = self._get_metadata(_suites)
            _add(layout, _base + 'release', "string:{}".format(_metadata))

        if has_graal_compiler:
            _add(layout, '<jre_base>/lib/jvmci/compiler-name', 'string:graal')

        super(BaseGraalVmLayoutDistribution, self).__init__(suite, name, deps, layout, path, platformDependent,
                                                            theLicense, exclLibs, path_substitutions=path_substitutions,
                                                            string_substitutions=string_substitutions,
                                                            testDistribution=testDistribution, **kw_args)
        self.reset_user_group = True
        mx.logv("'{}' has layout:\n{}".format(self.name, pprint.pformat(self.layout)))

    @staticmethod
    def _get_metadata(suites):
        """
        :type suites: list[mx.Suite]
        :return:
        """
        _commit_info = {}
        for _s in suites:
            if _s.vc:
                _info = _s.vc.parent_info(_s.vc_dir)
                _commit_info[_s.name] = {
                    "commit.rev": _s.vc.parent(_s.vc_dir),
                    "commit.committer": _info['committer'] if _s.vc.kind != 'binary' else 'unknown',
                    "commit.committer-ts": _info['committer-ts'],
                }
        _metadata = """\
OS_NAME={os}
OS_ARCH={arch}
SOURCE="{source}"
COMMIT_INFO={commit_info}
GRAALVM_VERSION={version}""".format(
            os=get_graalvm_os(),
            arch=mx.get_arch(),
            source=' '.join(['{}:{}'.format(_s.name, _s.version()) for _s in suites]),
            commit_info=json.dumps(_commit_info, sort_keys=True),
            version=_suite.release_version()
        )

        if _suite.is_release():
            catalog = _release_catalog()
        else:
            snapshot_catalog = _snapshot_catalog()
            catalog = "{}/{}".format(snapshot_catalog, _suite.vc.parent(_suite.vc_dir)) if snapshot_catalog else None

        if catalog:
            _metadata += "\ncomponent_catalog={}".format(catalog)

        return _metadata


if mx.is_windows():
    LayoutSuper = mx.LayoutZIPDistribution
else:
    LayoutSuper = mx.LayoutTARDistribution


class GraalVmLayoutDistribution(BaseGraalVmLayoutDistribution, LayoutSuper):  # pylint: disable=R0901
    def __init__(self, base_name, theLicense=None, stage1=False, **kw_args):
        self.base_name = base_name

        name, base_dir, self.vm_config_name, with_lib_polyglot, with_polyglot_launcher = _get_graalvm_configuration(base_name, stage1)

        super(GraalVmLayoutDistribution, self).__init__(
            suite=_suite,
            name=name,
            deps=[],
            components=registered_graalvm_components(stage1),
            is_graalvm=True,
            exclLibs=[],
            platformDependent=True,
            theLicense=theLicense,
            testDistribution=False,
            add_jdk_base=True,
            base_dir=base_dir,
            path=None,
            with_polyglot_launcher=with_polyglot_launcher,
            with_lib_polyglot=with_lib_polyglot,
            stage1=stage1,
            **kw_args)

    def getBuildTask(self, args):
        return GraalVmLayoutDistributionTask(args, self, 'latest_graalvm', 'latest_graalvm_home')


_graal_vm_configs_cache = {}


def _get_graalvm_configuration(base_name, stage1=False):
    key = base_name, stage1
    if key not in _graal_vm_configs_cache:
        components = registered_graalvm_components(stage1)
        components_set = set([c.short_name for c in components])

        if has_svm_polyglot_lib():
            components_set.add('libpoly')
            with_lib_polyglot = True
        else:
            with_lib_polyglot = False

        if _with_polyglot_launcher_project():
            with_polyglot_launcher = True
            components_set.add('poly')
            if not stage1 and _force_bash_launchers(get_polyglot_launcher_project().native_image_config):
                components_set.add('bpolyglot')
        else:
            with_polyglot_launcher = False

        if stage1:
            components_set.add('stage1')
        else:
            for component in components:
                for launcher_config in _get_launcher_configs(component):
                    if _force_bash_launchers(launcher_config):
                        components_set.add('b' + remove_exe_suffix(basename(launcher_config.destination)))
                for library_config in _get_library_configs(component):
                    if _skip_libraries(library_config):
                        components_set.add('s' + remove_lib_prefix_suffix(basename(library_config.destination)))
        if mx.get_opts().no_licenses:
            components_set.add('nolic')

        # Use custom distribution name and base dir for registered vm configurations
        vm_dist_name = None
        vm_config_name = None
        vm_config_additional_components = sorted(components_set)
        for dist_name, config_name, config_components, _, _ in mx_sdk_vm._vm_configs:
            config_components_set = set(config_components)
            config_additional_components = sorted(components_set - config_components_set)
            if config_components_set <= components_set and len(config_additional_components) <= len(vm_config_additional_components):
                vm_dist_name = dist_name.replace('-', '_')
                vm_config_name = config_name.replace('-', '_')
                vm_config_additional_components = config_additional_components

        if vm_dist_name:
            base_dir = base_name + '_' + vm_dist_name + '_java{}'.format(_src_jdk_version)
            name = base_dir
            if vm_config_additional_components:
                name += '_' + '_'.join(vm_config_additional_components)
        else:
            if mx.get_opts().verbose:
                mx.logv("No dist name for {}".format(vm_config_additional_components))
            base_dir = base_name + '_unknown' + '_java{}'.format(_src_jdk_version)
            name = base_dir + ('_stage1' if stage1 else '')
        name = name.upper()
        base_dir = base_dir.lower().replace('_', '-') + '-{}'.format(_suite.release_version())

        _graal_vm_configs_cache[key] = name, base_dir, vm_config_name, with_lib_polyglot, with_polyglot_launcher
    return _graal_vm_configs_cache[key]


class GraalVmLayoutDistributionTask(mx.LayoutArchiveTask):
    def __init__(self, args, dist, root_link_name, home_link_name):
        self._root_link_path = join(_suite.dir, root_link_name)
        self._home_link_path = join(_suite.dir, home_link_name)
        super(GraalVmLayoutDistributionTask, self).__init__(args, dist)

    def _add_link(self):
        if mx.get_os() == 'windows':
            mx.warn('Skip adding symlink to ' + self._home_link_target() + ' (Platform Windows)')
            return
        self._rm_link()
        os.symlink(self._root_link_target(), self._root_link_path)
        os.symlink(self._home_link_target(), self._home_link_path)

    def _root_link_target(self):
        return relpath(self.subject.output, _suite.dir)

    def _home_link_target(self):
        return relpath(join(self.subject.output, self.subject.jdk_base), _suite.dir)

    def _rm_link(self):
        if mx.get_os() == 'windows':
            return
        for l in [self._root_link_path, self._home_link_path]:
            if os.path.lexists(l):
                os.unlink(l)

    def needsBuild(self, newestInput):
        sup = super(GraalVmLayoutDistributionTask, self).needsBuild(newestInput)
        if sup[0]:
            return sup
        if mx.get_os() != 'windows' and self.subject == get_final_graalvm_distribution():
            for link_path, link_target in [(self._root_link_path, self._root_link_target()), (self._home_link_path, self._home_link_target())]:
                if not os.path.lexists(link_path):
                    return True, '{} does not exist'.format(link_path)
                link_file = mx.TimeStampFile(link_path, False)
                if link_file.isOlderThan(self.subject.output):
                    return True, '{} is older than {}'.format(link_file, newestInput)
                if link_target != os.readlink(link_path):
                    return True, '{} is pointing to the wrong directory'.format(link_file)
        return False, None

    def build(self):
        super(GraalVmLayoutDistributionTask, self).build()
        if self.subject == get_final_graalvm_distribution():
            self._add_link()

    def clean(self, forBuild=False):
        super(GraalVmLayoutDistributionTask, self).clean(forBuild)
        if self.subject == get_final_graalvm_distribution():
            self._rm_link()


class DebuginfoDistribution(mx.LayoutTARDistribution):  # pylint: disable=too-many-ancestors
    def __init__(self, subject_distribution, theLicense=None, **kw_args):
        super(DebuginfoDistribution, self).__init__(_suite,
                                                    name=subject_distribution.name + '_DEBUGINFO',
                                                    deps=[subject_distribution.name],
                                                    layout={},
                                                    path=None,
                                                    platformDependent=subject_distribution.platformDependent,
                                                    theLicense=theLicense, **kw_args)
        self._layout_initialized = False
        self.maven = _graalvm_maven_attributes
        self.subject_distribution = subject_distribution

    def _walk_layout(self):
        if not self._layout_initialized:
            root_contents = []
            self.layout = {
                './': root_contents
            }
            for dep_name in getattr(self.subject_distribution, 'buildDependencies', []):
                dep = mx.dependency(dep_name)
                if isinstance(dep, mx.JARDistribution):
                    if dep.is_stripped():
                        root_contents += ['dependency:{}:{}/*.map'.format(dep.suite.name, dep.name)]
                elif isinstance(dep, GraalVmNativeImage):
                    if dep.debug_file():
                        source_type = 'skip' if isinstance(dep.native_image_config, mx_sdk.LibraryConfig) and _skip_libraries(dep.native_image_config) else 'dependency'
                        root_contents += [source_type + ':{}:{}/*.debug'.format(dep.suite.name, dep.name)]
                        self.layout[dep.native_image_name + '-sources/'] = source_type + ':{}:{}/sources'.format(dep.suite.name, dep.name)
            self._layout_initialized = True
        return super(DebuginfoDistribution, self)._walk_layout()


def get_graalvm_os():
    os = mx.get_os()
    if os == 'darwin':
        return 'macos'
    return os


def remove_exe_suffix(name, require_suffix=True):
    if not _exe_suffix:
        return name
    if name.endswith(_exe_suffix):
        return name[:-len(_exe_suffix)]
    elif require_suffix:
        raise mx.abort("Missing exe suffix: " + name)
    return name


def remove_lib_prefix_suffix(libname, require_suffix_prefix=True):
    result = libname
    if _lib_suffix:
        if result.endswith(_lib_suffix):
            result = result[:-len(_lib_suffix)]
        elif require_suffix_prefix:
            raise mx.abort("Missing lib suffix: " + libname)
    if _lib_prefix:
        if result.startswith(_lib_prefix):
            result = result[len(_lib_prefix):]
        elif require_suffix_prefix:
            raise mx.abort("Missing lib prefix: " + libname)
    return result


class SvmSupport(object):
    def __init__(self):
        self._svm_supported = has_component('svm', stage1=True)
        self._debug_supported = self._svm_supported and has_component('svmee', stage1=True)
        self._pgo_supported = self._svm_supported and has_component('svmee', stage1=True)

    def is_supported(self):
        return self._svm_supported

    def native_image(self, build_args, output_file, allow_server=False, nonZeroIsFatal=True, out=None, err=None):
        assert self._svm_supported
        stage1 = get_stage1_graalvm_distribution()
        native_image_project_name = GraalVmLauncher.launcher_project_name(mx_sdk.LauncherConfig(mx.exe_suffix('native-image'), [], "", []), stage1=True)
        native_image_bin = join(stage1.output, stage1.find_single_source_location('dependency:' + native_image_project_name))
        native_image_command = [native_image_bin] + build_args
        # currently, when building with the bash version of native-image, --no-server is implied (and can not be passed)
        output_directory = dirname(output_file)
        native_image_command += [
            '-H:Path=' + output_directory or ".",
        ]
        return mx.run(native_image_command, nonZeroIsFatal=nonZeroIsFatal, out=out, err=err)

    def is_debug_supported(self):
        return self._debug_supported

    def is_pgo_supported(self):
        return self._debug_supported


def _get_svm_support():
    return SvmSupport()


class GraalVmProject(mx.Project):
    def __init__(self, component, name, deps, **kw_args):
        """
        :type component: mx_sdk.GraalVmComponent | None
        :type name:  str
        """
        self.component = component
        super(GraalVmProject, self).__init__(_suite, name, subDir=None, srcDirs=[], deps=deps, workingSets=None, d=_suite.dir, theLicense=None, **kw_args)

    def get_origin_suite(self):
        if self.component:
            return self.component.suite
        return self.suite


class GraalVmNativeProperties(GraalVmProject):
    def __init__(self, component, image_config, **kw_args):
        """
        :type component: mx_sdk.GraalVmComponent | None
        :type image_config: mx_sdk.AbstractNativeImageConfig
        """
        deps = []
        self.image_config = image_config
        super(GraalVmNativeProperties, self).__init__(component, GraalVmNativeProperties.project_name(image_config), deps=deps, **kw_args)

    @staticmethod
    def project_name(image_config):
        """
        :type image_config: mx_sdk.AbstractNativeImageConfig
        """
        return GraalVmNativeProperties.macro_name(image_config) + "_native-image.properties"

    @staticmethod
    def canonical_image_name(image_config):
        canonical_name = basename(image_config.destination)
        if isinstance(image_config, mx_sdk.LauncherConfig):
            canonical_name = remove_exe_suffix(canonical_name)
        elif isinstance(image_config, mx_sdk.LibraryConfig):
            canonical_name = remove_lib_prefix_suffix(canonical_name)
        return canonical_name

    @staticmethod
    def macro_name(image_config):
        macro_name = basename(image_config.destination)
        if isinstance(image_config, mx_sdk.LauncherConfig):
            macro_name = remove_exe_suffix(macro_name) + '-launcher'
        elif isinstance(image_config, mx_sdk.LibraryConfig):
            macro_name = remove_lib_prefix_suffix(macro_name) + '-library'
        return macro_name

    def getArchivableResults(self, use_relpath=True, single=False):
        out = self.properties_output_file()
        yield out, basename(out)
        if not single and isinstance(self.image_config, mx_sdk.LanguageLauncherConfig):
            out = self.polyglot_config_output_file()
            yield out, basename(out)

    def _output_file(self, filename):
        return join(self.get_output_base(), filename, GraalVmNativeProperties.macro_name(self.image_config), filename)

    def properties_output_file(self):
        return self._output_file("native-image.properties")

    def polyglot_config_output_file(self):
        return self._output_file("polyglot.config")

    def getBuildTask(self, args):
        return NativePropertiesBuildTask(self, args)


def _java_properties_escape(s, split_long=None, key_length=0):
    parts = []
    first = True
    if split_long and len(s) <= 80:
        split_long = False
    for c in s:
        if c == ' :=' and first:
            parts.append('\\')
            parts.append(c)
        elif c == '\t':
            parts.append('\\t')
        elif c == '\n':
            parts.append('\\n')
        elif c == '\r':
            parts.append('\\r')
        elif c == '\f':
            parts.append('\\f')
        elif c in '\\#!':
            parts.append('\\')
            parts.append(c)
        elif split_long and c == split_long:
            parts.append(c)
            parts.append('\\\n')
            parts.append(' ' * (key_length + 1))
        else:
            parts.append(c)
        first = False
    return ''.join(parts)


def _file_needs_build(newest_input, filepath, contents_getter):
    ts = mx.TimeStampFile(filepath)
    if not ts.exists():
        return filepath + " does not exist"
    if newest_input and ts.isOlderThan(newest_input):
        return "{} is older than {}".format(ts, newest_input)
    with open(filepath, 'r') as f:
        on_disk = unicode_utf8(f.read())
    if contents_getter() != on_disk:
        return "content not up to date"
    return None



class NativePropertiesBuildTask(mx.ProjectBuildTask):
    def __init__(self, subject, args):
        """
        :type subject: GraalVmNativeProperties
        """
        super(NativePropertiesBuildTask, self).__init__(args, 1, subject)
        self._contents = None
        self._polyglot_config_contents = None
        self._location_classpath = None

    def newestOutput(self):
        if isinstance(self.subject.image_config, mx_sdk.LanguageLauncherConfig):
            return mx.TimeStampFile(self.subject.polyglot_config_output_file())
        return mx.TimeStampFile(self.subject.properties_output_file())

    def __str__(self):
        graalvm_dist = get_final_graalvm_distribution()
        gralvm_location = graalvm_dist.find_single_source_location('dependency:' + self.subject.name)
        return "Creating native-image.properties for " + basename(dirname(gralvm_location))

    def polyglot_config_contents(self):
        if self._polyglot_config_contents is None:
            image_config = self.subject.image_config
            assert isinstance(image_config, mx_sdk.LanguageLauncherConfig)
            language = image_config.language
            classpath = self._get_location_classpath()
            main_class = image_config.main_class
            return u"|".join((language, u":".join(classpath), main_class))
        return self._polyglot_config_contents

    def _get_location_classpath(self):
        if self._location_classpath is None:
            graalvm_dist = get_final_graalvm_distribution()
            image_config = self.subject.image_config
            graalvm_location = dirname(graalvm_dist.find_single_source_location('dependency:' + self.subject.name))
            self._location_classpath = NativePropertiesBuildTask.get_launcher_classpath(graalvm_dist, graalvm_location, image_config, self.subject.component)
        return self._location_classpath

    @staticmethod
    def get_launcher_classpath(graalvm_dist, start, image_config, component):
        location_cp = graalvm_home_relative_classpath(image_config.jar_distributions, start, graal_vm=graalvm_dist)
        location_classpath = location_cp.split(os.pathsep) if location_cp else []
        if image_config.dir_jars:
            if not component:
                raise mx.abort("dir_jars=True can only be used on launchers associated with a component")
            component_dir = _get_component_type_base(component, apply_substitutions=True)
            dir_name = component.dir_name
            if dir_name:
                component_dir = component_dir + dir_name + '/'
            component_dir_rel = relpath(component_dir, start)
            if not component_dir_rel.endswith('/'):
                component_dir_rel += '/'
            location_classpath.append(component_dir_rel + '*')
        return location_classpath

    def contents(self):
        if self._contents is None:
            image_config = self.subject.image_config
            build_args = [
                '--no-fallback',
                '--initialize-at-build-time',
                '-H:+EnforceMaxRuntimeCompileMethods',
                '-Dorg.graalvm.version={}'.format(_suite.release_version()),
            ]
            graalvm_dist = get_final_graalvm_distribution()
            if graalvm_dist.vm_config_name:
                build_args += ['-Dorg.graalvm.config={}'.format(graalvm_dist.vm_config_name.upper())]
            if _debug_images():
                build_args += ['-ea', '-H:-AOTInline', '-H:+PreserveFramePointer']
            if _get_svm_support().is_debug_supported():
                build_args += ['-g']

            graalvm_location = dirname(graalvm_dist.find_single_source_location('dependency:' + self.subject.name))
            location_classpath = self._get_location_classpath()
            graalvm_home = _get_graalvm_archive_path("")

            if isinstance(image_config, mx_sdk.LibraryConfig):
                suffix = _lib_suffix
                build_args.append('--shared')
                project_name_f = GraalVmNativeImage.project_name
            elif isinstance(image_config, mx_sdk.LauncherConfig):
                suffix = _exe_suffix
                project_name_f = GraalVmLauncher.launcher_project_name
            else:
                raise mx.abort("Unsupported image config type: " + str(type(image_config)))

            if isinstance(image_config, mx_sdk.LanguageLauncherConfig):
                build_args += ['--language:' + image_config.language, '--tool:all']
            elif isinstance(image_config, mx_sdk.LauncherConfig) and 'PolyglotLauncher' in image_config.main_class:
                build_args += ['-Dcom.oracle.graalvm.launcher.macrospaths=${.}/..']

            source_type = 'skip' if isinstance(image_config, mx_sdk.LibraryConfig) and _skip_libraries(image_config) else 'dependency'
            graalvm_image_destination = graalvm_dist.find_single_source_location(source_type + ':' + project_name_f(image_config))

            if isinstance(image_config, mx_sdk.LauncherConfig):
                if image_config.is_sdk_launcher:
                    build_args += [
                        '-H:-ParseRuntimeOptions',
                        '-Dorg.graalvm.launcher.classpath=' + ':'.join(graalvm_home_relative_classpath(image_config.jar_distributions, graalvm_home).split(os.pathsep)),
                    ]

                build_args += [
                    '--features=org.graalvm.home.HomeFinderFeature',
                    '-Dorg.graalvm.launcher.relative.home=' + relpath(graalvm_image_destination, graalvm_home)
                ]

                for language, path in image_config.relative_home_paths.items():
                    build_args += ['-Dorg.graalvm.launcher.relative.' + language + '.home=' + path]

            build_args += [mx_subst.string_substitutions.substitute(arg) for arg in image_config.build_args]

            name = basename(image_config.destination)
            if suffix:
                name = name[:-len(suffix)]
            canonical_name = GraalVmNativeProperties.canonical_image_name(image_config)
            build_args += _extra_image_builder_args(canonical_name)
            profiles = _image_profile(canonical_name)
            if profiles:
                if not _get_svm_support().is_pgo_supported():
                    raise mx.abort("Image profiles can not be used if PGO is not supported.")
                basenames = [basename(p) for p in profiles]
                if len(set(basenames)) != len(profiles):
                    raise mx.abort("Profiles for an image must have unique filenames.\nThis is not the case for {}: {}.".format(canonical_name, profiles))
                build_args += ['--pgo=' + ','.join(('${.}/' + n for n in basenames))]

            requires = [arg[2:] for arg in build_args if arg.startswith('--language:') or arg.startswith('--tool:') or arg.startswith('--macro:')]
            build_args = [arg for arg in build_args if not (arg.startswith('--language:') or arg.startswith('--tool:') or arg.startswith('--macro:'))]

            if any((' ' in arg for arg in build_args)):
                mx.abort("Unsupported space in launcher build argument: {} in config for {}".format(image_config.build_args, image_config.destination))

            self._contents = u""

            def _write_ln(s):
                self._contents += s + u"\n"

            myself = basename(__file__)
            if myself.endswith('.pyc'):
                myself = myself[:-1]
            _write_ln(u"# Generated with \u2764 by " + myself)
            _write_ln(u'ImageName=' + _java_properties_escape(name))
            _write_ln(u'ImagePath=' + _java_properties_escape("${.}/" + relpath(dirname(graalvm_image_destination), graalvm_location).replace(os.sep, '/')))
            if requires:
                _write_ln(u'Requires=' + _java_properties_escape(' '.join(requires), ' ', len('Requires')))
            if isinstance(image_config, mx_sdk.LauncherConfig):
                _write_ln(u'ImageClass=' + _java_properties_escape(image_config.main_class))
            _write_ln(u'ImageClasspath=' + _java_properties_escape(':'.join(("${.}/" + e.replace(os.sep, '/') for e in location_classpath)), ':', len('ImageClasspath')))
            _write_ln(u'Args=' + _java_properties_escape(' '.join(build_args), ' ', len('Args')))
        return self._contents

    def build(self):
        with mx.SafeFileCreation(self.subject.properties_output_file()) as sfc, io.open(sfc.tmpFd, mode='w', closefd=False, encoding='utf-8') as f:
            f.write(self.contents())
        if isinstance(self.subject.image_config, mx_sdk.LanguageLauncherConfig):
            with mx.SafeFileCreation(self.subject.polyglot_config_output_file()) as sfc, io.open(sfc.tmpFd, mode='w', closefd=False, encoding='utf-8') as f:
                f.write(self.polyglot_config_contents())

    def needsBuild(self, newestInput):
        sup = super(NativePropertiesBuildTask, self).needsBuild(newestInput)
        if sup[0]:
            return sup
        reason = _file_needs_build(newestInput, self.subject.properties_output_file(), self.contents)
        if not reason and isinstance(self.subject.image_config, mx_sdk.LanguageLauncherConfig):
            reason = _file_needs_build(newestInput, self.subject.polyglot_config_output_file(), self.polyglot_config_contents)
        if reason:
            return True, reason
        return False, None

    def clean(self, forBuild=False):
        if exists(self.subject.properties_output_file()):
            os.unlink(self.subject.properties_output_file())
        if exists(self.subject.polyglot_config_output_file()):
            os.unlink(self.subject.polyglot_config_output_file())


class GraalVmJvmciParentClasspath(GraalVmProject):  # based on GraalVmNativeProperties
    def __init__(self, jvmci_parent_jars, **kw_args):
        """
        :type jvmci_parent_jars: list[str]
        """
        assert jvmci_parent_jars
        self.jvmci_parent_jars = jvmci_parent_jars
        super(GraalVmJvmciParentClasspath, self).__init__(None, GraalVmJvmciParentClasspath.project_name(), deps=[], **kw_args)

    @staticmethod
    def project_name():
        return 'jvmci-parent-classpath'

    def output_file(self):
        return join(self.get_output_base(), GraalVmJvmciParentClasspath.output_file_name(), GraalVmJvmciParentClasspath.project_name(), GraalVmJvmciParentClasspath.output_file_name())

    @staticmethod
    def output_file_name():
        return 'parentClassLoader.classpath'

    def getArchivableResults(self, use_relpath=True, single=False):
        _output_file = self.output_file()
        yield _output_file, basename(_output_file)

    def getBuildTask(self, args):
        return JvmciParentClasspathBuildTask(self, args)


class JvmciParentClasspathBuildTask(mx.ProjectBuildTask):  # based NativePropertiesBuildTask
    def __init__(self, subject, args):
        """
        :type subject: GraalVmJvmciParentClasspath
        """
        super(JvmciParentClasspathBuildTask, self).__init__(args, 1, subject)
        graalvm_dist = get_final_graalvm_distribution()
        start_path = dirname(graalvm_dist.find_single_source_location('dependency:{}'.format(GraalVmJvmciParentClasspath.project_name())))
        cp_entries = [relpath(graalvm_dist.find_single_source_location('dependency:{}'.format(jpj)), start_path) for jpj in self.subject.jvmci_parent_jars]
        self._contents = unicode_utf8(_platform_classpath(cp_entries))

    def contents(self):
        return self._contents

    def newestOutput(self):
        return mx.TimeStampFile(self.subject.output_file())

    def __str__(self):
        return "Creating '{}' file".format(GraalVmJvmciParentClasspath.output_file_name())

    def build(self):
        with mx.SafeFileCreation(self.subject.output_file()) as sfc, io.open(sfc.tmpFd, mode='w', closefd=False, encoding='utf-8') as f:
            f.write(self.contents())

    def needsBuild(self, newestInput):
        sup = super(JvmciParentClasspathBuildTask, self).needsBuild(newestInput)
        if sup[0]:
            return sup
        reason = _file_needs_build(newestInput, self.subject.output_file(), self.contents)
        if reason:
            return True, reason
        return False, None

    def clean(self, forBuild=False):
        _output_file = self.subject.output_file()
        if exists(_output_file):
            os.unlink(_output_file)


class GraalVmJImage(mx.Project):
    """
    A GraalVmJImage produces the lib/modules jimage in a JDK9+ based GraalVM.
    """
    __metaclass__ = ABCMeta

    def __init__(self, suite, jimage_jars, workingSets, theLicense=None, **kw_args):
        super(GraalVmJImage, self).__init__(suite=suite, name='graalvm-jimage', subDir=None, srcDirs=[], deps=jimage_jars,
                                            workingSets=workingSets, d=_suite.dir, theLicense=theLicense,
                                            **kw_args)

    def isPlatformDependent(self):
        return True

    def getBuildTask(self, args):
        return GraalVmJImageBuildTask(self, args)

    def output_directory(self):
        return join(self.get_output_base(), self.name)

    def output_witness(self):
        return join(self.output_directory(), 'lib', 'modules')

    def getArchivableResults(self, use_relpath=True, single=False):
        if single:
            raise ValueError("{} only produces multiple output".format(self))
        out_dir = self.output_directory()
        if exists(out_dir):
            logical_root = out_dir
            for root, _, files in os.walk(out_dir):
                for name in files:
                    yield join(root, name), join(relpath(root, logical_root), name)


class GraalVmJImageBuildTask(mx.ProjectBuildTask):
    def __init__(self, subject, args):
        super(GraalVmJImageBuildTask, self).__init__(args, 1, subject)

    def build(self):
        with_source = lambda dep: not isinstance(dep, mx.Dependency) or (_include_sources() and dep.isJARDistribution() and not dep.is_stripped())
        mx_sdk.jlink_new_jdk(_src_jdk, self.subject.output_directory(), self.subject.deps, with_source=with_source)
        with open(self._config_file(), 'w') as f:
            f.write('\n'.join(self._config()))

    def needsBuild(self, newestInput):
        sup = super(GraalVmJImageBuildTask, self).needsBuild(newestInput)
        if sup[0]:
            return sup
        out_file = self.newestOutput()
        if not out_file.exists():
            return True, '{} does not exist'.format(out_file.path)
        if newestInput and out_file.isOlderThan(newestInput):
            return True, '{} is older than {}'.format(out_file, newestInput)
        with open(self._config_file(), 'r') as f:
            old_config = [l.strip() for l in f.readlines()]
            if set(old_config) != set(self._config()):
                return True, 'the configuration changed'
        return False, None

    def newestOutput(self):
        return mx.TimeStampFile(self.subject.output_witness())

    def clean(self, forBuild=False):
        out_dir = self.subject.output_directory()
        if exists(out_dir):
            mx.rmtree(out_dir)

    def __str__(self):
        return 'Building {}'.format(self.subject.name)

    def _config(self):
        return ['include sources: {}'.format(_include_sources()), 'strip jars: {}'.format(mx.get_opts().strip_jars)]

    def _config_file(self):
        return self.subject.output_directory() + '.config'


class GraalVmNativeImage(_with_metaclass(ABCMeta, GraalVmProject)):
    def __init__(self, component, name, deps, native_image_config, **kw_args): # pylint: disable=super-init-not-called
        """
        :type component: mx_sdk.GraalVmComponent | None
        :type native_image_config: mx_sdk.AbstractNativeImageConfig
        """
        assert isinstance(native_image_config, mx_sdk.AbstractNativeImageConfig), type(native_image_config).__name__
        self.native_image_config = native_image_config
        self.native_image_jar_distributions = list(native_image_config.jar_distributions)
        svm_support = _get_svm_support()
        if svm_support.is_supported():
            deps += self.native_image_jar_distributions
        super(GraalVmNativeImage, self).__init__(component, name=name, deps=deps, **kw_args)
        if svm_support.is_supported() and self.is_native():
            if not hasattr(self, 'buildDependencies'):
                self.buildDependencies = []
            self.buildDependencies += ['{}:{}'.format(_suite, get_stage1_graalvm_distribution_name())]

    def getArchivableResults(self, use_relpath=True, single=False):
        yield self.output_file(), self.native_image_name
        if not single:
            debug = self.debug_file()
            if debug:
                yield debug, basename(debug)
            src_dir = self.image_sources_dir()
            if exists(src_dir):
                logical_root = dirname(src_dir)
                for root, _, files in os.walk(src_dir):
                    for name in files:
                        yield join(root, name), join(relpath(root, logical_root), name)

    def debug_file(self):
        if not self.is_native():
            return None
        if _get_svm_support().is_debug_supported() and mx.get_os() == 'linux':
            return join(self.get_output_base(), self.name, self.native_image_name + '.debug')
        return None

    @property
    def native_image_name(self):
        return basename(self.native_image_config.destination)

    def output_file(self):
        return join(self.get_output_base(), self.name, self.native_image_name)

    def image_sources_dir(self):
        return join(self.get_output_base(), self.name, "sources")

    def isPlatformDependent(self):
        return True

    @staticmethod
    def project_name(native_image_config):
        return basename(native_image_config.destination) + ".image"

    def is_native(self):
        return True


class GraalVmLauncher(_with_metaclass(ABCMeta, GraalVmNativeImage)):
    def __init__(self, component, name, deps, native_image_config, stage1=False, **kw_args): # pylint: disable=super-init-not-called
        """
        :type native_image_config: mx_sdk.LauncherConfig
        """
        assert isinstance(native_image_config, mx_sdk.LauncherConfig), type(native_image_config).__name__
        self.stage1 = stage1
        super(GraalVmLauncher, self).__init__(component, name, deps, native_image_config, **kw_args)

    def getBuildTask(self, args):
        if self.is_native():
            return GraalVmSVMLauncherBuildTask(self, args, _get_svm_support())
        else:
            return GraalVmBashLauncherBuildTask(self, args)

    def is_native(self):
        return GraalVmLauncher.is_launcher_native(self.native_image_config, self.stage1)

    @property
    def native_image_name(self):
        super_name = super(GraalVmLauncher, self).native_image_name
        if mx.get_os() == 'windows':
            if not self.is_native():
                return os.path.splitext(super_name)[0] + '.cmd'
        return super_name

    def get_containing_graalvm(self):
        if self.stage1:
            return get_stage1_graalvm_distribution()
        else:
            return get_final_graalvm_distribution()

    @staticmethod
    def launcher_project_name(native_image_config, stage1=False):
        is_bash = not GraalVmLauncher.is_launcher_native(native_image_config, stage1)
        return GraalVmNativeImage.project_name(native_image_config) + ("-bash" if is_bash else "") + ("-stage1" if stage1 else "")

    @staticmethod
    def is_launcher_native(native_image_config, stage1=False):
        return _get_svm_support().is_supported() and not stage1 and not _force_bash_launchers(native_image_config)

    @staticmethod
    def get_launcher_destination(config, stage1):
        """
        :type config: mx_sdk.LauncherConfig
        :type stage1: bool
        """
        if mx.get_os() == 'windows':
            if not GraalVmLauncher.is_launcher_native(config, stage1):
                return os.path.splitext(config.destination)[0] + '.cmd'
        return config.destination


class GraalVmPolyglotLauncher(GraalVmLauncher):         #pylint: disable=too-many-ancestors
    def __init__(self, component, deps, launcherConfig, **kw_args):
        launcher_config = mx_sdk.LauncherConfig(**launcherConfig)
        super(GraalVmPolyglotLauncher, self).__init__(component, GraalVmLauncher.launcher_project_name(launcher_config), deps, launcher_config, **kw_args)


class GraalVmLibrary(GraalVmNativeImage):
    def __init__(self, component, name, deps, native_image_config, **kw_args):
        assert isinstance(native_image_config, mx_sdk.LibraryConfig), type(native_image_config).__name__
        super(GraalVmLibrary, self).__init__(component, name, deps, native_image_config=native_image_config, **kw_args)

        svm_support = _get_svm_support()
        assert svm_support.is_supported(), "Needs svm to build " + str(self)
        if not hasattr(self, 'buildDependencies'):
            self.buildDependencies = []
        self.buildDependencies += ['{}:{}'.format(_suite, get_stage1_graalvm_distribution_name())]

    def getBuildTask(self, args):
        svm_support = _get_svm_support()
        assert svm_support.is_supported(), "Needs svm to build " + str(self)
        if not self.is_skipped():
            return GraalVmLibraryBuildTask(self, args, svm_support)
        else:
            return mx.NoOpTask(self, args)

    def getArchivableResults(self, use_relpath=True, single=False):
        if self.is_skipped():
            yield None, None
            return
        for e in super(GraalVmLibrary, self).getArchivableResults(use_relpath=use_relpath, single=single):
            yield e
            if single:
                return
        output_dir = dirname(self.output_file())
        for e in os.listdir(output_dir):
            absolute_path = join(output_dir, e)
            if isfile(absolute_path) and e.endswith('.h'):
                yield absolute_path, e

    def is_skipped(self):
        return _skip_libraries(self.native_image_config)


class GraalVmMiscLauncher(GraalVmLauncher):     #pylint: disable=too-many-ancestors
    def __init__(self, component, native_image_config, stage1=False, **kw_args):
        super(GraalVmMiscLauncher, self).__init__(component, GraalVmLauncher.launcher_project_name(native_image_config, stage1=stage1), [], native_image_config, stage1=stage1, **kw_args)


class GraalVmLanguageLauncher(GraalVmLauncher): #pylint: disable=too-many-ancestors
    def __init__(self, component, native_image_config, stage1=False, **kw_args):
        super(GraalVmLanguageLauncher, self).__init__(component, GraalVmLauncher.launcher_project_name(native_image_config, stage1=stage1), [], native_image_config, stage1=stage1, **kw_args)


class GraalVmNativeImageBuildTask(_with_metaclass(ABCMeta, mx.ProjectBuildTask)):
    def needsBuild(self, newestInput):
        sup = super(GraalVmNativeImageBuildTask, self).needsBuild(newestInput)
        if sup[0]:
            return sup
        out_file = mx.TimeStampFile(self.subject.output_file())
        if not out_file.exists():
            return True, '{} does not exist'.format(out_file.path)
        if newestInput and out_file.isOlderThan(newestInput):
            return True, '{} is older than {}'.format(out_file, newestInput)
        reason = self.native_image_needs_build(out_file)
        if reason:
            return True, reason
        return False, None

    def native_image_needs_build(self, out_file):
        # TODO check if definition has changed
        return None

    def newestOutput(self):
        return mx.TimeStampFile(self.subject.output_file())

    def clean(self, forBuild=False):
        out_file = self.subject.output_file()
        if exists(out_file):
            os.unlink(out_file)

    def __str__(self):
        return 'Building {}'.format(self.subject.name)


class GraalVmBashLauncherBuildTask(GraalVmNativeImageBuildTask):
    def __init__(self, subject, args):
        """
        :type subject: GraalVmNativeImage
        """
        super(GraalVmBashLauncherBuildTask, self).__init__(args, 1, subject)

    def _template_file(self):
        ext = 'cmd' if mx.get_os() == 'windows' else 'sh'
        _custom_launcher = self.subject.native_image_config.custom_bash_launcher
        if _custom_launcher:
            return join(self.subject.suite.dir, _custom_launcher + "." + ext)
        return join(_suite.mxDir, 'vm', 'launcher_template.' + ext)

    def native_image_needs_build(self, out_file):
        sup = super(GraalVmBashLauncherBuildTask, self).native_image_needs_build(out_file)
        if sup:
            return sup
        if out_file.isOlderThan(self._template_file()):
            return 'template {} updated'.format(self._template_file())
        return None

    def build(self):
        output_file = self.subject.output_file()
        mx.ensure_dir_exists(dirname(output_file))
        graal_vm = self.subject.get_containing_graalvm()
        script_destination_directory = dirname(graal_vm.find_single_source_location('dependency:' + self.subject.name))
        if _src_jdk_version >= 9:
            jre_bin = _get_graalvm_archive_path('bin', graal_vm=graal_vm)
        else:
            jre_bin = _get_graalvm_archive_path('jre/bin', graal_vm=graal_vm)

        def _get_classpath():
            cp = NativePropertiesBuildTask.get_launcher_classpath(graal_vm, script_destination_directory, self.subject.native_image_config, self.subject.component)
            return os.pathsep.join(cp)

        def _get_jre_bin():
            return relpath(jre_bin, script_destination_directory)

        def _get_main_class():
            return self.subject.native_image_config.main_class

        def _get_extra_jvm_args():
            return ' '.join(self.subject.native_image_config.extra_jvm_args)

        _template_subst = mx_subst.SubstitutionEngine(mx_subst.string_substitutions)
        _template_subst.register_no_arg('classpath', _get_classpath)
        _template_subst.register_no_arg('jre_bin', _get_jre_bin)
        _template_subst.register_no_arg('main_class', _get_main_class)
        _template_subst.register_no_arg('extra_jvm_args', _get_extra_jvm_args)

        with open(self._template_file(), 'r') as template, mx.SafeFileCreation(output_file) as sfc, open(sfc.tmpPath, 'w') as launcher:
            for line in template:
                launcher.write(_template_subst.substitute(line))
        os.chmod(output_file, 0o755)


def _get_graalvm_archive_path(jdk_path, graal_vm=None):
    if graal_vm is None:
        graal_vm = get_final_graalvm_distribution()
    if graal_vm.jdk_base and graal_vm.jdk_base != '.':
        if jdk_path and jdk_path != '.':
            return graal_vm.jdk_base + '/' + jdk_path
        else:
            return graal_vm.jdk_base
    return jdk_path


# Those libraries are optional runtime dependencies of SVM
_known_missing_jars = {
    'HAMCREST',
    'JUNIT',
    'JUNIT_TOOL',
    'JLINE',
    'TRUFFLE_DEBUG',
    'NANO_HTTPD',
    'NANO_HTTPD_WEBSERVER',
    'JFFI',
    'JNR_FFI',
    'JNR_INVOKE',
    'JFFI_NATIVE',
    'JNR_POSIX',
    'JNR_CONSTANTS',
    'JDK_TOOLS',
}


def graalvm_home_relative_classpath(dependencies, start=None, with_boot_jars=False, graal_vm=None):
    if graal_vm is None:
        graal_vm = get_final_graalvm_distribution()
    start = start or _get_graalvm_archive_path('', graal_vm=graal_vm)
    assert start.startswith(_get_graalvm_archive_path('', graal_vm=graal_vm)), start + " does not start with " + _get_graalvm_archive_path('', graal_vm=graal_vm)
    """:type : GraalVmLayoutDistribution"""
    boot_jars_directory = "jre/lib/boot"
    if graal_vm.jdk_base and graal_vm.jdk_base != '.':
        assert not graal_vm.jdk_base.endswith('/')
        boot_jars_directory = graal_vm.jdk_base + "/" + boot_jars_directory
    _cp = set()
    jimage = mx.project('graalvm-jimage', fatalIfMissing=False)
    jimage_deps = jimage.deps if jimage else None
    mx.logv("Composing classpath for " + str(dependencies) + ". Entries:\n" + '\n'.join(('- {}:{}'.format(d.suite, d.name) for d in mx.classpath_entries(dependencies))))
    for _cp_entry in mx.classpath_entries(dependencies):
        if jimage_deps and _cp_entry in jimage_deps:
            continue
        if _cp_entry.isJdkLibrary() or _cp_entry.isJreLibrary():
            jdk = _src_jdk
            if hasattr(_cp_entry, 'jdkStandardizedSince') and jdk.javaCompliance >= _cp_entry.jdkStandardizedSince:
                continue
            jdk_location = relpath(_cp_entry.classpath_repr(jdk), jdk.home)
            graalvm_location = join(graal_vm.jdk_base, jdk_location)
        else:
            graalvm_location = graal_vm.find_single_source_location('dependency:{}:{}'.format(_cp_entry.suite, _cp_entry.name), fatal_if_missing=False)
            if graalvm_location is None and _cp_entry.isDistribution():
                # Try to find an overlapping distribution
                for _, layout_source in graal_vm._walk_layout():
                    if layout_source['source_type'] == 'dependency' and layout_source['path'] is None:
                        d = mx.dependency(layout_source['dependency'])
                        if d.isDistribution():
                            if _cp_entry in d.overlapped_distributions() and set(_cp_entry.archived_deps()) <= set(d.archived_deps()):
                                mx.logv("{}:{} is not available in GraalVM, replacing with {}:{}".format(_cp_entry.suite, _cp_entry.name, d.suite, d.name))
                                graalvm_location = graal_vm.find_single_source_location('dependency:{}:{}'.format(d.suite, d.name))
                                break
            if graalvm_location is None:
                if _cp_entry.name in _known_missing_jars:
                    mx.warn("Skipping known missing dependency {} when building classpath for {}".format(_cp_entry.name, dependencies))
                    continue
                mx.abort("Could not find '{}:{}' in GraalVM ('{}')".format(_cp_entry.suite, _cp_entry.name, graal_vm.name))
        if not with_boot_jars and (graalvm_location.startswith(boot_jars_directory) or _cp_entry.isJreLibrary()):
            continue
        _cp.add(relpath(graalvm_location, start))
    return os.pathsep.join(_cp)


class GraalVmSVMNativeImageBuildTask(GraalVmNativeImageBuildTask):
    def __init__(self, subject, args, svm_support):
        """
        :type subject: GraalVmNativeImage
        :type svm_support: SvmSupport
        """
        super(GraalVmSVMNativeImageBuildTask, self).__init__(args, min(8, mx.cpu_count()), subject)
        self.svm_support = svm_support

    def build(self):
        build_args = self.get_build_args()
        output_file = self.subject.output_file()
        mx.ensure_dir_exists(dirname(output_file))

        # Disable build server (different Java properties on each build prevent server reuse)
        self.svm_support.native_image(build_args, output_file)

        with open(self._get_command_file(), 'w') as f:
            f.writelines((l + os.linesep for l in build_args))

    def native_image_needs_build(self, out_file):
        sup = super(GraalVmSVMNativeImageBuildTask, self).native_image_needs_build(out_file)
        if sup:
            return sup
        previous_build_args = []
        command_file = self._get_command_file()
        if exists(command_file):
            with open(command_file) as f:
                previous_build_args = [l.rstrip('\r\n') for l in f.readlines()]
        args = self.get_build_args()
        if previous_build_args != args:
            mx.logv("{} != {}".format(previous_build_args, args))
            return 'image command changed'
        return None

    def _get_command_file(self):
        return self.subject.output_file() + '.cmd'

    def get_build_args(self):
        build_args = [
            '--macro:' + GraalVmNativeProperties.macro_name(self.subject.native_image_config),
            '-H:NumberOfThreads=' + str(self.parallelism),
        ]
        if self.subject.native_image_config.is_polyglot:
            build_args += ["--macro:truffle", "--language:all"]
        return build_args


class GraalVmSVMLauncherBuildTask(GraalVmSVMNativeImageBuildTask):
    pass


class GraalVmLibraryBuildTask(GraalVmSVMNativeImageBuildTask):
    pass


class InstallableComponentArchiver(mx.Archiver):
    def __init__(self, path, components, **kw_args):
        """
        :type path: str
        :type components: list[mx_sdk.GraalVmLanguage]
        :type kind: str
        :type reset_user_group: bool
        :type duplicates_action: str
        :type context: object
        """
        super(InstallableComponentArchiver, self).__init__(path, **kw_args)
        self.components = components
        self.permissions = []
        self.symlinks = []

    @staticmethod
    def _perm_str(filename):
        _perm = str(oct(os.lstat(filename).st_mode)[-3:])
        _str = ''
        for _p in _perm:
            if _p == '7':
                _str += 'rwx'
            elif _p == '6':
                _str += 'rw-'
            elif _p == '5':
                _str += 'r-x'
            elif _p == '4':
                _str += 'r--'
            elif _p == '0':
                _str += '---'
            else:
                mx.abort('File {} has unsupported permission {}'.format(filename, _perm))
        return _str

    def add(self, filename, archive_name, provenance):
        self.permissions.append('{} = {}'.format(archive_name, self._perm_str(filename)))
        super(InstallableComponentArchiver, self).add(filename, archive_name, provenance)

    def add_str(self, data, archive_name, provenance):
        self.permissions.append('{} = {}'.format(archive_name, 'rw-rw-r--'))
        super(InstallableComponentArchiver, self).add_str(data, archive_name, provenance)

    def add_link(self, target, archive_name, provenance):
        self.permissions.append('{} = {}'.format(archive_name, 'rwxrwxrwx'))
        self.symlinks.append('{} = {}'.format(archive_name, target))
        # do not add symlinks, use the metadata to create them

    def __exit__(self, exc_type, exc_value, traceback):
        main_component = self.components[0]
        _manifest_str = """Bundle-Name: {name}
Bundle-Symbolic-Name: org.graalvm.{id}
Bundle-Version: {version}
Bundle-RequireCapability: org.graalvm; filter:="(&(graalvm_version={version})(os_name={os})(os_arch={arch}))"
x-GraalVM-Polyglot-Part: {polyglot}
""".format(  # GR-10249: the manifest file must end with a newline
            name=main_component.name,
            id=main_component.installable_id,
            version=_suite.release_version(),
            os=get_graalvm_os(),
            arch=mx.get_arch(),
            polyglot=isinstance(main_component, mx_sdk.GraalVmTruffleComponent) and main_component.include_in_polyglot
                     and (not isinstance(main_component, mx_sdk.GraalVmTool) or main_component.include_by_default)
        )
        dependencies = set()
        # `known_allowed_dependencies` is a workaround for GR-18947
        known_allowed_dependencies = {"llvm-toolchain"}
        for comp in self.components:
            for c in comp.direct_dependencies():
                d = get_installable_distribution(c.name, fatalIfMissing=False)
                if d:
                    if c.installable_id in known_allowed_dependencies:
                        dependencies.add(c.installable_id)
                    else:
                        mx.warn("Ignoring dependency to {} in {} installable".format(c.installable_id, main_component.name))
        dependencies = sorted(dependencies)
        if dependencies:
            _manifest_str += "Require-Bundle: {}\n".format(','.join(("org.graalvm." + d for d in dependencies)))
        if isinstance(main_component, mx_sdk.GraalVmLanguage):
            _manifest_str += """x-GraalVM-Working-Directories: {workdir}
""".format(workdir=join('jre', 'languages', main_component.dir_name))

        post_install_msg = None
        for component in self.components:
            if getattr(component, 'post_install_msg', None):
                if post_install_msg:
                    post_install_msg = post_install_msg + '\n' + component.post_install_msg
                else:
                    post_install_msg = component.post_install_msg
        if post_install_msg:
            _manifest_str += """x-GraalVM-Message-PostInst: {msg}
""".format(msg=post_install_msg.replace("\\", "\\\\").replace("\n", "\\n"))

        _manifest_lines = []
        for l in _manifest_str.split('\n'):
            _first = True
            while len(l) > 72:
                _manifest_lines += [("" if _first else " ") + l[:72]]
                l = l[72:]
                _first = False
            if len(l) > 0:
                _manifest_lines += [("" if _first else " ") + l]

        _manifest_str_wrapped = '\n'.join(_manifest_lines) + "\n"
        _manifest_arc_name = 'META-INF/MANIFEST.MF'

        _permissions_str = '\n'.join(self.permissions)
        _permissions_arc_name = 'META-INF/permissions'

        _symlinks_str = '\n'.join(self.symlinks)
        _symlinks_arc_name = 'META-INF/symlinks'

        for _str, _arc_name in [(_manifest_str_wrapped, _manifest_arc_name), (_permissions_str, _permissions_arc_name),
                                (_symlinks_str, _symlinks_arc_name)]:
            self.add_str(_str, _arc_name, '{}<-string:{}'.format(_arc_name, _str))

        super(InstallableComponentArchiver, self).__exit__(exc_type, exc_value, traceback)


class GraalVmInstallableComponent(BaseGraalVmLayoutDistribution, mx.LayoutJARDistribution):  # pylint: disable=R0901
    def __init__(self, component, extra_components=None, **kw_args):
        """
        :type component: mx_sdk.GraalVmComponent
        """
        self.main_component = component

        def create_archive(path, **_kw_args):
            return InstallableComponentArchiver(path, self.components, **_kw_args)

        launcher_configs = list(_get_launcher_configs(component))
        for component_ in extra_components:
            launcher_configs += _get_launcher_configs(component_)

        other_involved_components = []
        if self.main_component.short_name not in ('svm', 'svmee') and _get_svm_support().is_supported() and launcher_configs:
            other_involved_components += [c for c in registered_graalvm_components(stage1=True) if c.short_name in ('svm', 'svmee')]

        name = '{}_INSTALLABLE'.format(component.installable_id.replace('-', '_').upper())
        for launcher_config in launcher_configs:
            if _force_bash_launchers(launcher_config):
                name += '_B' + basename(launcher_config.destination).upper()
        if other_involved_components:
            name += '_' + '_'.join(sorted((component.short_name.upper() for component in other_involved_components)))
        self.maven = _graalvm_maven_attributes
        components = [component]
        if extra_components:
            components += extra_components
        super(GraalVmInstallableComponent, self).__init__(
            suite=_suite,
            name=name,
            deps=[],
            components=components,
            is_graalvm=False,
            exclLibs=[],
            platformDependent=True,
            theLicense=None,
            testDistribution=False,
            archive_factory=create_archive,
            path=None,
            **kw_args)


class GraalVmStandaloneComponent(mx.LayoutTARDistribution):  # pylint: disable=too-many-ancestors
    def __init__(self, component, graalvm, **kw_args):
        """
        :type component: mx_sdk.GraalVmTruffleComponent
        :type graalvm: GraalVmLayoutDistribution
        """
        other_comp_names = []
        if _get_svm_support().is_supported() and _get_launcher_configs(component):
            other_comp_names += [c.short_name for c in registered_graalvm_components(stage1=True) if c.short_name in ('svm', 'svmee')]

        self.main_comp_dir_name = component.dir_name

        name = '_'.join([self.main_comp_dir_name, 'standalone'] + other_comp_names).upper().replace('-', '_')
        self.base_dir_name = graalvm.string_substitutions.substitute(component.standalone_dir_name)
        base_dir = './{}/'.format(self.base_dir_name)
        layout = {}

        # Compute paths from standalone component launchers to other homes
        home_paths = {}
        for dependency_name, details in component.standalone_dependencies.items():
            dependency_path = details[0]
            comp = get_component(dependency_name, fatalIfMissing=True)
            home_paths[comp.installable_id] = base_dir + dependency_path

        def is_jar_distribution(val):
            return val['source_type'] == 'dependency' and isinstance(mx.dependency(val['dependency'], fatalIfMissing=False), mx.JARDistribution)

        def add_files_from_component(comp, path_prefix, excluded_paths):
            component_base_dir = graalvm.path_substitutions.substitute(_get_component_type_base(comp))
            support_dir_pattern = component_base_dir + comp.dir_name + '/'
            launcher_configs = _get_launcher_configs(comp)

            for path, source in graalvm._walk_layout():
                if path.startswith(support_dir_pattern):
                    path_from_home = path.split(support_dir_pattern, 1)[1]
                    # take only the distributions that are not JAR distributions
                    if not is_jar_distribution(source) and path_from_home not in excluded_paths:
                        new_path = path_prefix + path_from_home
                        layout.setdefault(new_path, []).append(source)

            for launcher_config in launcher_configs:
                destination = path_prefix + launcher_config.destination
                for language, path_from_root in home_paths.items():
                    relative_path_from_launcher_dir = relpath(path_from_root, dirname(destination))
                    launcher_config.add_relative_home_path(language, relative_path_from_launcher_dir)

        add_files_from_component(component, base_dir, [])

        for dependency_name, details in component.standalone_dependencies.items():
            dependency_path = details[0]
            excluded_paths = details[1] if len(details) > 1 else []
            dependency = get_component(dependency_name, fatalIfMissing=True)
            excluded_paths = [mx_subst.path_substitutions.substitute(excluded) for excluded in excluded_paths]
            add_files_from_component(dependency, base_dir + dependency_path + '/', excluded_paths)

        mx.logv("Standalone '{}' has layout:\n{}".format(name, pprint.pformat(layout)))

        self.maven = _graalvm_maven_attributes
        super(GraalVmStandaloneComponent, self).__init__(
            suite=_suite,
            name=name,
            deps=[],
            layout=layout,
            path=None,
            platformDependent=True,
            theLicense=None,
            path_substitutions=graalvm.path_substitutions,
            string_substitutions=graalvm.string_substitutions,
            **kw_args)


_vm_suite = 'uninitialized'
_final_graalvm_distribution = 'uninitialized'
_stage1_graalvm_distribution = 'uninitialized'
_lib_polyglot_project = 'uninitialized'
_polyglot_launcher_project = 'uninitialized'


def _platform_classpath(cp_entries):
    return os.pathsep.join(mx.normpath(entry) for entry in cp_entries)


def get_stage1_graalvm_distribution_name():
    name, _, _, _, _ = _get_graalvm_configuration('GraalVM', True)
    return name


def get_stage1_graalvm_distribution():
    """:rtype: GraalVmLayoutDistribution"""
    global _stage1_graalvm_distribution
    if _stage1_graalvm_distribution == 'uninitialized':
        _stage1_graalvm_distribution = GraalVmLayoutDistribution(_graalvm_base_name, stage1=True)
        _stage1_graalvm_distribution.description = "GraalVM distribution (stage1)"
        _stage1_graalvm_distribution.maven = False
    return _stage1_graalvm_distribution


def get_final_graalvm_distribution():
    """:rtype: GraalVmLayoutDistribution"""
    global _final_graalvm_distribution
    if _final_graalvm_distribution == 'uninitialized':
        _final_graalvm_distribution = GraalVmLayoutDistribution(_graalvm_base_name)
        _final_graalvm_distribution.description = "GraalVM distribution"
        _final_graalvm_distribution.maven = _graalvm_maven_attributes
    return _final_graalvm_distribution


def get_installable_distribution(name, fatalIfMissing=True):
    """
    :type name: str Component name
    :rtype: GraalVmInstallableComponent
    """
    installables = _get_dists(GraalVmInstallableComponent)
    if installables:
        for installable in installables:
            if installable.main_component.name == name:
                return installable
        if fatalIfMissing:
            raise mx.abort("Cannot find an installable with component name '{}'.\nAvailable installables:\n{}".format(name, '\n'.join((('- ' + s.main_component.name for s in installables)))))
    elif fatalIfMissing:
        raise mx.abort('No installables available. Did you forget to dynamically import a component?')
    return None


def get_standalone_distribution(comp_dir_name):
    """
    :type comp_dir_name: str
    :rtype: GraalVmStandaloneComponent
    """
    standalones = _get_dists(GraalVmStandaloneComponent)
    if standalones:
        for standalone in standalones:
            if standalone.main_comp_dir_name == comp_dir_name:
                return standalone
        raise mx.abort("Cannot find a standalone with dir_name '{}'.\nAvailable standalones:\n{}".format(comp_dir_name, '\n'.join((('- ' + s.main_comp_dir_name for s in standalones)))))
    else:
        raise mx.abort('No standalones available. Did you forget to dynamically import a component?')


def has_svm_polyglot_lib():
    if not _get_svm_support().is_supported() or not _with_polyglot_lib_project():
        return False
    return any(c.has_polyglot_lib_entrypoints for c in registered_graalvm_components(stage1=False))

def get_lib_polyglot_project():
    global _lib_polyglot_project
    if _lib_polyglot_project == 'uninitialized':
        if not has_svm_polyglot_lib():
            _lib_polyglot_project = None
        else:
            polyglot_lib_build_args = []
            polyglot_lib_jar_dependencies = []
            polyglot_lib_build_dependencies = []
            has_polyglot_lib_entrypoints = False
            if "LIBPOLYGLOT_DISABLE_BACKGROUND_COMPILATION" in os.environ:
                polyglot_lib_build_args += ["-R:-TruffleBackgroundCompilation"]
            for component in registered_graalvm_components(stage1=False):
                has_polyglot_lib_entrypoints |= component.has_polyglot_lib_entrypoints
                polyglot_lib_build_args += component.polyglot_lib_build_args
                polyglot_lib_jar_dependencies += component.polyglot_lib_jar_dependencies
                polyglot_lib_build_dependencies += component.polyglot_lib_build_dependencies

            assert has_polyglot_lib_entrypoints
            lib_polyglot_config = mx_sdk.LibraryConfig(
                destination="<lib:polyglot>",
                jar_distributions=polyglot_lib_jar_dependencies,
                build_args=[
                    "-H:+IncludeAllTimeZones",
                    "-Dgraalvm.libpolyglot=true",
                    "-Dorg.graalvm.polyglot.install_name_id=@rpath/jre/lib/polyglot/<lib:polyglot>",
                    "--tool:all",
                ] + polyglot_lib_build_args,
                is_polyglot=True,
            )
            _lib_polyglot_project = GraalVmLibrary(None, GraalVmNativeImage.project_name(lib_polyglot_config), [], lib_polyglot_config)

            if polyglot_lib_build_dependencies:
                if not hasattr(_lib_polyglot_project, 'buildDependencies'):
                    _lib_polyglot_project.buildDependencies = []
                _lib_polyglot_project.buildDependencies += polyglot_lib_build_dependencies
    return _lib_polyglot_project


def get_polyglot_launcher_project():
    """:rtype: GraalVmPolyglotLauncher"""
    global _polyglot_launcher_project
    if _polyglot_launcher_project == 'uninitialized':
        if _with_polyglot_launcher_project():
            _polyglot_launcher_project = GraalVmPolyglotLauncher(
                component=None,
                deps=[],
                launcherConfig={
                    "build_args": [
                        "-H:-ParseRuntimeOptions",
                        "-H:Features=org.graalvm.launcher.PolyglotLauncherFeature",
                        "--tool:all",
                    ],
                    "jar_distributions": [
                        "sdk:LAUNCHER_COMMON",
                    ],
                    "main_class": "org.graalvm.launcher.PolyglotLauncher",
                    "destination": "<exe:polyglot>",
                    "is_sdk_launcher": True,
                    "is_polyglot": True,
                }
            )
        else:
            _polyglot_launcher_project = None
    return _polyglot_launcher_project


_native_image_configs = {}


def _get_launcher_configs(component):
    """ :rtype : list[mx_sdk.LauncherConfig]"""
    return _get_native_image_configs(component, 'launcher_configs')


def _get_library_configs(component):
    """ :rtype : list[mx_sdk.LibraryConfig]"""
    return _get_native_image_configs(component, 'library_configs')


def _get_native_image_configs(component, config_type):
    if _native_image_configs.get(config_type) is None:
        new_configs = {}
        for component_ in registered_graalvm_components(stage1=True):
            for config in getattr(component_, config_type):
                config_name = config.destination
                if config_name in new_configs:
                    _, prev_component = new_configs[config_name]
                    if prev_component.priority > component_.priority:
                        continue
                    if prev_component.priority == component_.priority:
                        raise mx.abort("Conflicting native-image configs: {} and {} both declare a config called {}".format(component_.name, prev_component.name, config_name))
                new_configs[config_name] = config, component_
        configs = {}
        for config, component_ in new_configs.values():
            configs.setdefault(component_.name, []).append(config)
        _native_image_configs[config_type] = configs
    return _native_image_configs.get(config_type).get(component.name, [])


def has_vm_suite():
    global _vm_suite
    if _vm_suite == 'uninitialized':
        _vm_suite = mx.suite('vm', fatalIfMissing=False)
    return _vm_suite is not None


def mx_register_dynamic_suite_constituents(register_project, register_distribution):
    """
    :type register_project: (mx.Project) -> None
    :type register_distribution: (mx.Distribution) -> None
    """
    with_debuginfo = []
    if has_component('FastR'):
        fastr_release_env = mx.get_env('FASTR_RELEASE', None)
        if fastr_release_env is None:
            mx.abort("When including FastR, please set FASTR_RELEASE to true (env FASTR_RELEASE=true mx ...). Got FASTR_RELEASE={}".format(fastr_release_env))
        if mx.get_env('FASTR_RFFI') not in (None, ''):
            mx.abort("When including FastR, FASTR_RFFI should not be set. Got FASTR_RFFI=" + mx.get_env('FASTR_RFFI'))

    register_distribution(get_final_graalvm_distribution())
    with_debuginfo.append(get_final_graalvm_distribution())

    # Add the macros if SubstrateVM is included, as images could be created later with an installable Native Image
    with_svm = has_component('svm')
    names = set()
    short_names = set()
    needs_stage1 = False
    installables = {}
    jvmci_parent_jars = []

    for component in registered_graalvm_components(stage1=False):
        if component.name in names:
            mx.abort("Two components are named '{}'. The name should be unique".format(component.name))
        if component.short_name in short_names:
            mx.abort("Two components have short name '{}'. The short names should be unique".format(component.short_name))
        names.add(component.name)
        short_names.add(component.short_name)
        jvmci_parent_jars.extend(component.jvmci_parent_jars)
        if register_project:
            if isinstance(component, mx_sdk.GraalVmTruffleComponent):
                config_class = GraalVmLanguageLauncher
            else:
                config_class = GraalVmMiscLauncher
            for launcher_config in _get_launcher_configs(component):
                launcher_project = config_class(component, launcher_config)
                register_project(launcher_project)
                if launcher_project.is_native():
                    needs_stage1 = True
                if with_svm:
                    register_project(GraalVmNativeProperties(component, launcher_config))
            for library_config in _get_library_configs(component):
                register_project(GraalVmLibrary(component, GraalVmNativeImage.project_name(library_config), [], library_config))
                assert with_svm
                register_project(GraalVmNativeProperties(component, library_config))
                if not _skip_libraries(library_config.destination):
                    needs_stage1 = True
        # The JS components have issues ATM since they share the same directory
        if not _disable_installable(component) and (component.installable or (isinstance(component, mx_sdk.GraalVmLanguage) and component.dir_name != 'js')):
            installables.setdefault(component.installable_id, []).append(component)

    # Create installables
    for components in installables.values():
        main_component = min(components, key=lambda c: c.priority)
        installable_component = GraalVmInstallableComponent(main_component, extra_components=[c for c in components if c != main_component])
        register_distribution(installable_component)
        with_debuginfo.append(installable_component)

    # Create standalones
    for components in installables.values():
        main_component = min(components, key=lambda c: c.priority)
        if isinstance(main_component, mx_sdk.GraalVmTruffleComponent) and has_svm_launcher(main_component):
            dependencies = main_component.standalone_dependencies.keys()
            missing_dependencies = [dep for dep in dependencies if not has_component(dep)]
            if missing_dependencies:
                if mx.get_opts().verbose:
                    mx.warn("Skipping standalone {} because the components {} are excluded".format(main_component.name, missing_dependencies))
            else:
                standalone = GraalVmStandaloneComponent(get_component(main_component.name, fatalIfMissing=True), get_final_graalvm_distribution())
                register_distribution(standalone)
                with_debuginfo.append(standalone)

    if register_project:
        lib_polyglot_project = get_lib_polyglot_project()
        if lib_polyglot_project:
            needs_stage1 = True
            register_project(lib_polyglot_project)
            assert with_svm
            register_project(GraalVmNativeProperties(None, lib_polyglot_project.native_image_config))

        polyglot_launcher_project = get_polyglot_launcher_project()
        if polyglot_launcher_project:
            needs_stage1 = True
            register_project(polyglot_launcher_project)
            if with_svm:
                register_project(GraalVmNativeProperties(None, polyglot_launcher_project.native_image_config))

        if _src_jdk_version == 8 and jvmci_parent_jars:
            register_project(GraalVmJvmciParentClasspath(jvmci_parent_jars))

        if _src_jdk.javaCompliance >= '9':
            jimage_jars = set()
            for component in registered_graalvm_components(stage1=False):
                jimage_jars.update(component.boot_jars + component.jvmci_parent_jars)
                if isinstance(component, mx_sdk.GraalVmJvmciComponent):
                    jimage_jars.update(component.jvmci_jars)

            register_project(GraalVmJImage(
                suite=_suite,
                jimage_jars=list(jimage_jars),
                workingSets=None,
            ))

    if needs_stage1:
        if register_project:
            for component in registered_graalvm_components(stage1=True):
                if isinstance(component, mx_sdk.GraalVmTruffleComponent):
                    config_class = GraalVmLanguageLauncher
                else:
                    config_class = GraalVmMiscLauncher
                for launcher_config in _get_launcher_configs(component):
                    register_project(config_class(component, launcher_config, stage1=True))
        register_distribution(get_stage1_graalvm_distribution())

    if _with_debuginfo():
        if _get_svm_support().is_debug_supported() or mx.get_opts().strip_jars:
            for d in with_debuginfo:
                register_distribution(DebuginfoDistribution(d))


def has_svm_launcher(component, fatalIfMissing=False):
    """
    :type component: mx_sdk.GraalVmComponent | str
    :type fatalIfMissing: bool
    :rtype: bool
    """
    component = get_component(component, fatalIfMissing) if isinstance(component, str) else component
    result = _get_svm_support().is_supported() and not _has_forced_launchers(component) and bool(component.launcher_configs)
    if fatalIfMissing and not result:
        hint = None
        if _has_forced_launchers(component):
            hint = "Are you forcing bash launchers?"
        elif not bool(component.launcher_configs):
            hint = "Does '{}' register launcher configs?".format(component.name)
        mx.abort("'{}' does not have a native launcher.".format(component.name) + ("\n" + hint if hint else ""))
    return result


def has_svm_launchers(components, fatalIfMissing=False):
    """
    :type components: list[mx_sdk.GraalVmComponent | str]
    :type fatalIfMissing: bool
    :rtype: bool
    """
    return all((has_svm_launcher(component, fatalIfMissing=fatalIfMissing) for component in components))


def get_native_image_locations(name, image_name):
    libgraal_libs = [l for l in _get_library_configs(get_component(name)) if image_name in basename(l.destination)]
    if libgraal_libs:
        library_config = libgraal_libs[0]
        dist = get_final_graalvm_distribution()
        source_type = 'skip' if _skip_libraries(library_config) else 'dependency'
        return join(graalvm_output_root(), dist.find_single_source_location(source_type + ':' + GraalVmLibrary.project_name(library_config)))
    return None


def get_component(name, fatalIfMissing=False, stage1=False):
    """
    :type name: str
    :type fatalIfMissing: bool
    :type stage1: bool
    :rtype: mx_sdk.GraalVmComponent | None
    """
    for c in registered_graalvm_components(stage1=stage1):
        if name in (c.short_name, c.name):
            return c
    if fatalIfMissing:
        mx.abort("'{}' is not registered as GraalVM component. Did you forget to dynamically import it?".format(name))
    return None


def has_component(name, stage1=False):
    """
    :type name: str
    :type stage1: bool
    :rtype: bool
    """
    return get_component(name, fatalIfMissing=False, stage1=stage1) is not None


def has_components(names, stage1=False):
    """
    :type names: list[str]
    :type stage1: bool
    :rtype: bool
    """
    return all((has_component(name, stage1=stage1) for name in names))


def graalvm_output_root():
    return join(_suite.dir, get_final_graalvm_distribution().output)


def graalvm_output():
    return join(graalvm_output_root(), get_final_graalvm_distribution().jdk_base)


def graalvm_dist_name():
    return get_final_graalvm_distribution().name


def graalvm_version():
    return _suite.release_version()


def graalvm_home(fatalIfMissing=False):
    _graalvm_dist = get_final_graalvm_distribution()
    _graalvm_home = join(_graalvm_dist.output, _graalvm_dist.jdk_base)
    if fatalIfMissing and not exists(_graalvm_home):
        mx.abort("GraalVM home '{}' does not exist. Did you forget to build with this set of dynamic imports and mx options?".format(_graalvm_home))
    return _graalvm_home


def standalone_home(comp_dir_name):
    _standalone_dist = get_standalone_distribution(comp_dir_name)
    return join(_standalone_dist.output, _standalone_dist.base_dir_name)


def log_graalvm_dist_name(args):
    """print the name of the GraalVM distribution"""
    parser = ArgumentParser(prog='mx graalvm-dist-name', description='Print the name of the GraalVM distribution')
    _ = parser.parse_args(args)
    mx.log(graalvm_dist_name())


def log_graalvm_version(args):
    """print the GraalVM version"""
    parser = ArgumentParser(prog='mx graalvm-version', description='Print the GraalVM version')
    _ = parser.parse_args(args)
    mx.log(graalvm_version())


def log_graalvm_home(args):
    """print the GraalVM home dir"""
    parser = ArgumentParser(prog='mx graalvm-home', description='Print the GraalVM home directory')
    _ = parser.parse_args(args)
    mx.log(graalvm_home())


def log_standalone_home(args):
    """print the GraalVM standalone home dir"""
    parser = ArgumentParser(prog='mx standalone-home', description='Print the standalone home directory')
    parser.add_argument('comp_dir_name', action='store', help='component dir name', metavar='<comp_dir_name>')
    args = parser.parse_args(args)
    mx.log(standalone_home(args.comp_dir_name))


def graalvm_show(args):
    """print the GraalVM config"""
    parser = ArgumentParser(prog='mx graalvm-show', description='Print the GraalVM config')
    parser.add_argument('--stage1', action='store_true', help='show the components for stage1')
    args = parser.parse_args(args)

    graalvm_dist = get_final_graalvm_distribution()
    mx.log("GraalVM distribution: {}".format(graalvm_dist))
    mx.log("Version: {}".format(_suite.release_version()))
    mx.log("Config name: {}".format(graalvm_dist.vm_config_name))
    mx.log("Components:")
    for component in registered_graalvm_components(stage1=args.stage1):
        mx.log(" - {} ('{}', /{})".format(component.name, component.short_name, component.dir_name))

    launchers = [p for p in _suite.projects if isinstance(p, GraalVmLauncher) and p.get_containing_graalvm() == graalvm_dist]
    if launchers:
        mx.log("Launchers:")
        for launcher in launchers:
            suffix = ''
            profile_cnt = len(_image_profile(GraalVmNativeProperties.canonical_image_name(launcher.native_image_config)))
            if profile_cnt > 0:
                suffix += " ({} pgo profile file{})".format(profile_cnt, 's' if profile_cnt > 1 else '')
            mx.log(" - {} ({}){}".format(launcher.native_image_name, "native" if launcher.is_native() else "bash", suffix))
    else:
        mx.log("No launcher")

    libraries = [p for p in _suite.projects if isinstance(p, GraalVmLibrary)]
    if libraries:
        mx.log("Libraries:")
        for library in libraries:
            suffix = ''
            if library.is_skipped():
                suffix += " (skipped)"
            profile_cnt = len(_image_profile(GraalVmNativeProperties.canonical_image_name(library.native_image_config)))
            if profile_cnt > 0:
                suffix += " ({} pgo profile file{})".format(profile_cnt, 's' if profile_cnt > 1 else '')
            mx.log(" - {}{}".format(library.native_image_name, suffix))
    else:
        mx.log("No library")

    installables = _get_dists(GraalVmInstallableComponent)
    if installables:
        mx.log("Installables:")
        for i in installables:
            mx.log(" - {}".format(i))
    else:
        mx.log("No installable")

    standalones = _get_dists(GraalVmStandaloneComponent)
    if standalones:
        mx.log("Standalones:")
        for s in standalones:
            mx.log(" - {}".format(s))
    else:
        mx.log("No standalone")


def _get_dists(dist_class):
    """
    :type dist_class: mx.Distribution
    :rtype: list[mx.Distribution]
    """
    return [d for d in _suite.dists if isinstance(d, dist_class)]


def _env_var_to_bool(name, default='false'):
    val = mx.get_env(name, default)
    b = _str_to_bool(val)
    if isinstance(b, bool):
        return b
    else:
        raise mx.abort("Invalid boolean env var value {}={}; expected: <true | false>".format(name, val))


def _str_to_bool(val):
    """
    :type val: str
    :rtype: str | bool
    """
    low_val = val.lower()
    if low_val in ('false', '0', 'no'):
        return False
    elif low_val in ('true', '1', 'yes'):
        return True
    return val


def check_versions(jdk, graalvm_version_regex, expect_graalvm, check_jvmci):
    """
    :type jdk: mx.JDKConfig | str
    :type graalvm_version_regex: typing.Pattern
    :type expect_graalvm: bool
    :type check_jvmci: bool
    """
    check_env = "Please check the value of the 'JAVA_HOME' environment variable, your mx 'env' files, and the documentation of this suite"

    if isinstance(jdk, str):
        assert isdir(jdk), 'Not a directory: ' + jdk
        jdk = mx.JDKConfig(jdk)
    out = mx.OutputCapture()
    if check_jvmci and mx.run([jdk.java, '-XX:+JVMCIPrintProperties'], nonZeroIsFatal=False, out=out, err=out):
        mx.log_error(out.data)
        mx.abort("'{}' is not a JVMCI-enabled JDK ('java -XX:+JVMCIPrintProperties' fails).\n{}.".format(jdk.home, check_env))

    out = _decode(subprocess.check_output([jdk.java, '-version'], stderr=subprocess.STDOUT)).rstrip()

    jdk_version = jdk.version
    if jdk_version < mx.VersionSpec('1.8') or mx.VersionSpec('9') <= jdk_version < mx.VersionSpec('11'):
        mx.abort("GraalVM requires JDK8 or >=JDK11 as base-JDK, while the selected JDK ('{}') is '{}':\n{}\n\n{}.".format(jdk.home, jdk_version, out, check_env))

    match = graalvm_version_regex.match(out)
    if expect_graalvm and match is None:
        mx.abort("'{}' is not a GraalVM. Its version string:\n{}\ndoes not match:\n{}".format(jdk.home, out, graalvm_version_regex.pattern))
    elif expect_graalvm and match.group('graalvm_version') != _suite.release_version():
        mx.abort("'{}' has a wrong GraalVM version:\n{}\nexpected:\n{}".format(jdk.home, match.group('graalvm_version'), _suite.release_version()))
    elif not expect_graalvm and match:
        mx.abort("GraalVM cannot be built using a GraalVM as base-JDK ('{}').\n{}.".format(jdk.home, check_env))


def log_graalvm_vm_name(args):
    """Print the VM name of GraalVM"""
    parser = ArgumentParser(prog='mx graalvm-vm-name', description='Print the VM name of GraalVM')
    _ = parser.parse_args(args)
    mx.log(graalvm_vm_name(get_final_graalvm_distribution(), _src_jdk))


def graalvm_vm_name(graalvm_dist, jdk):
    """
    :type jdk_home: str
    :rtype str:
    """
    out = _decode(subprocess.check_output([jdk.java, '-version'], stderr=subprocess.STDOUT)).rstrip()
    match = re.search(r'^(?P<base_vm_name>[a-zA-Z() ]+64-Bit )Server VM', out.split('\n')[-1])
    vm_name = match.group('base_vm_name') if match else ''
    vm_name += '{} {}'.format(graalvm_dist.base_name, graalvm_dist.vm_config_name.upper()) if graalvm_dist.vm_config_name else graalvm_dist.base_name
    vm_name += ' {}'.format(graalvm_version())
    return vm_name


mx.add_argument('--components', action='store', help='Comma-separated list of component names to build. Only those components and their dependencies are built.')
mx.add_argument('--exclude-components', action='store', help='Comma-separated list of component names to be excluded from the build.')
mx.add_argument('--disable-libpolyglot', action='store_true', help='Disable the \'polyglot\' library project.')
mx.add_argument('--disable-polyglot', action='store_true', help='Disable the \'polyglot\' launcher project.')
mx.add_argument('--disable-installables', action='store', help='Disable the \'installable\' distributions for gu.'
                                                               'This can be a comma-separated list of disabled components short names or `true` to disable all installables.', default=None)
mx.add_argument('--debug-images', action='store_true', help='Build native images in debug mode: \'-H:-AOTInline\' and with \'-ea\'.')
mx.add_argument('--native-images', action='store', help='Comma-separated list of launchers and libraries (syntax: lib:polyglot) to build with Native Image.')
mx.add_argument('--force-bash-launchers', action='store', help='Force the use of bash launchers instead of native images.'
                                                               'This can be a comma-separated list of disabled launchers or `true` to disable all native launchers.', default=None)
mx.add_argument('--skip-libraries', action='store', help='Do not build native images for these libraries.'
                                                         'This can be a comma-separated list of disabled libraries or `true` to disable all libraries.', default=None)
mx.add_argument('--no-sources', action='store_true', help='Do not include the archives with the source files of open-source components.')
mx.add_argument('--with-debuginfo', action='store_true', help='Generate debuginfo distributions.')
mx.add_argument('--snapshot-catalog', action='store', help='Change the default URL of the component catalog for snapshots.', default=None)
mx.add_argument('--release-catalog', action='store', help='Change the default URL of the component catalog for releases.', default=None)
mx.add_argument('--extra-image-builder-argument', action='append', help='Add extra arguments to the image builder.', default=[])
mx.add_argument('--image-profile', action='append', help='Add a profile to be used while building a native image.', default=[])
mx.add_argument('--no-licenses', action='store_true', help='Do not add license files in the archives.')

def _debug_images():
    return mx.get_opts().debug_images or _env_var_to_bool('DEBUG_IMAGES')

def _components_include_list():
    included = mx.get_opts().components or mx.get_env('COMPONENTS', None)
    if included is None:
        return None
    return [mx_sdk.graalvm_component_by_name(name) for name in included.split(',')]

def _excluded_components():
    excluded = mx.get_opts().exclude_components or mx.get_env('EXCLUDE_COMPONENTS', '')
    return excluded.split(',')


def _extra_image_builder_args(image):
    prefix = image + ':'
    prefix_len = len(prefix)
    args = []
    for arg in mx.get_opts().extra_image_builder_argument or mx.get_env('EXTRA_IMAGE_BUILDER_ARGUMENTS', '').split():
        if arg.startswith(prefix):
            args.append(arg[prefix_len:])
        elif arg.startswith('-'):
            args.append(arg)
    return args


def _image_profile(image):
    prefix = image + ':'
    prefix_len = len(prefix)
    profiles = []
    for arg in mx.get_opts().image_profile or mx.get_env('IMAGE_PROFILES', '').split(';'):
        if arg.startswith(prefix):
            profiles += arg[prefix_len:].split(',')
    return profiles


def _with_polyglot_lib_project():
    return not (mx.get_opts().disable_libpolyglot or _env_var_to_bool('DISABLE_LIBPOLYGLOT'))


def _with_polyglot_launcher_project():
    return not (mx.get_opts().disable_polyglot or _env_var_to_bool('DISABLE_POLYGLOT'))


def _force_bash_launchers(launcher):
    """
    :type launcher: str | mx_sdk.AbstractNativeImageConfig
    """
    if isinstance(launcher, mx_sdk.AbstractNativeImageConfig):
        launcher = launcher.destination
    launcher = remove_exe_suffix(launcher, require_suffix=False)
    launcher_name = basename(launcher)

    only = mx.get_opts().native_images or mx.get_env('NATIVE_IMAGES', None)
    if only:
        only = [lib for lib in only.split(',') if not lib.startswith('lib:')]
        return launcher_name not in only

    forced = _str_to_bool(mx.get_opts().force_bash_launchers or mx.get_env('FORCE_BASH_LAUNCHERS', 'false' if has_vm_suite() else 'true'))
    if isinstance(forced, bool):
        return forced
    else:
        return launcher_name in forced.split(',')


def _skip_libraries(library):
    """
    :type library: str | mx_sdk.AbstractNativeImageConfig
    """
    if isinstance(library, mx_sdk.AbstractNativeImageConfig):
        library = library.destination
    library_name = remove_lib_prefix_suffix(basename(library), require_suffix_prefix=False)

    only = mx.get_opts().native_images or mx.get_env('NATIVE_IMAGES', None)
    if only:
        only = [lib[4:] for lib in only.split(',') if lib.startswith('lib:')]
        return library_name not in only

    skipped = _str_to_bool(mx.get_opts().skip_libraries or mx.get_env('SKIP_LIBRARIES', 'false' if has_vm_suite() else 'true'))
    if isinstance(skipped, bool):
        return skipped
    else:
        return library_name in skipped.split(',')


def _disable_installable(component):
    """ :type component: str | mx_sdk.GraalVmComponent """
    disabled = _str_to_bool(mx.get_opts().disable_installables or mx.get_env('DISABLE_INSTALLABLES', 'false' if has_vm_suite() else 'true'))
    if isinstance(disabled, bool):
        return disabled
    if isinstance(disabled, str):
        disabled = disabled.split(',')
    if isinstance(component, mx_sdk.GraalVmComponent):
        component = component.short_name
    return component in disabled


def _has_forced_launchers(component):
    """:type component: mx_sdk.GraalVmComponent"""
    for launcher_config in _get_launcher_configs(component):
        if _force_bash_launchers(launcher_config):
            return True
    return False


def _include_sources():
    return not (mx.get_opts().no_sources or _env_var_to_bool('NO_SOURCES'))


def _with_debuginfo():
    return mx.get_opts().with_debuginfo or _env_var_to_bool('WITH_DEBUGINFO')


def _snapshot_catalog():
    return mx.get_opts().snapshot_catalog or mx.get_env('SNAPSHOT_CATALOG')


def _release_catalog():
    return mx.get_opts().release_catalog or mx.get_env('RELEASE_CATALOG')


def mx_post_parse_cmd_line(args):
    if _src_jdk_version >= 9:
        for component in registered_graalvm_components():
            for boot_jar in component.boot_jars:
                if not mx.get_module_name(mx.distribution(boot_jar)):
                    mx.abort("Component '{}' declares a boot jar distribution ('{}') that does not define a module.\nPlease set 'moduleInfo' or 'moduleName'.".format(component.name, boot_jar))


mx.update_commands(_suite, {
    'graalvm-dist-name': [log_graalvm_dist_name, ''],
    'graalvm-version': [log_graalvm_version, ''],
    'graalvm-home': [log_graalvm_home, ''],
    'graalvm-show': [graalvm_show, ''],
    'graalvm-vm-name': [log_graalvm_vm_name, ''],
    'standalone-home': [log_standalone_home, 'comp-dir-name'],
})
