import sys
import tempfile
from collections import OrderedDict
from ConfigParser import ConfigParser, NoOptionError, NoSectionError

import functools32
from scipy.weave.build_tools import build_extension
from Cython.Compiler.Main import compile, default_options
from path_helpers import path


class HDict(OrderedDict):
    def __hash__(self):
        return hash(frozenset(self.items()))


class HList(list):
    def __hash__(self):
        return hash(frozenset(self))


class HostBuilder(object):
    def __call__(self, extension_c_path, build_dir, **kwargs):
        extension_c_path = str(extension_c_path)
        return build_extension(extension_c_path, build_dir=build_dir, **kwargs)


class NvccBuilder(object):
    def __call__(self, extension_c_path, build_dir, **kwargs):
        from theano.sandbox.cuda.nvcc_compiler import NVCC_compiler

        extension_c_path = path(extension_c_path)
        module_name = extension_c_path.namebase
        print extension_c_path, module_name
        try:
            NVCC_compiler.compile_str(module_name,
                                      extension_c_path.bytes(),
                                      build_dir, py_module=False,
                                      **kwargs)
            return True
        except:
            return False


class Context(object):
    def __init__(self, cythrust_rcd=path('~/.config/cythrust').expand(),
                 builder=None):
        if not cythrust_rcd.isdir():
            cythrust_rcd.makedirs_p()
        self.cythrust_rcd = cythrust_rcd

        self.cythrust_rc = cythrust_rcd.joinpath('cythrustrc')

        self.config = ConfigParser()
        self.config.read([self.cythrust_rc])
        self._build_cache_root = None
        if builder is None:
            builder = HostBuilder()
        self.builder = builder

    @property
    def build_cache_root(self):
        try:
            return path(self.config.get('code_gen',
                                        'build_cache_root')).expand()
        except NoSectionError:
            self.config.add_section('code_gen')
        except NoOptionError:
            pass
        self.build_cache_root = '~/.cache/cythrust'
        return self.build_cache_root

    @build_cache_root.setter
    def build_cache_root(self, value):
        # Set default build cache root directory.
        self.config.set('code_gen', 'build_cache_root', value)

    def save_config(self, output_file=None):
        if output_file is None:
            output_file = self.cythrust_rc
        # Writing our configuration file to 'example.cfg'
        with open(output_file, 'wb') as configfile:
            self.config.write(configfile)

    def __del__(self):
        self.save_config()

    def build_pyx(self, pyx_source_path, module_name=None, module_dir=None,
                  pyx_kwargs=None, **distutils_kwargs):
        if pyx_kwargs is None:
            pyx_kwargs = {}
        build_cache_root = self.build_cache_root
        if not build_cache_root.isdir():
            build_cache_root.makedirs_p()

        pyx_source_path = path(pyx_source_path).expand()
        if module_name is None:
            module_name = pyx_source_path.namebase
        build_dir = path(tempfile.mkdtemp(prefix='temp_%s__' % module_name,
                                          dir=build_cache_root))
        try:
            source_file = build_dir.joinpath(module_name + '.pyx')
            pyx_source_path.copy(source_file)

            # If there is a Cython `pxd` header, copy it to the build
            # directory.
            header_file = pyx_source_path.parent.joinpath(
                pyx_source_path.namebase + '.pxd')
            if header_file.isfile():
                header_file.copy(source_file.parent.joinpath(module_name +
                                                             '.pxd'))
            else:
                print '"%s" is not a file.' % header_file

            compile_result = compile(source_file, default_options,
                                     **pyx_kwargs)
            if module_dir is None:
                module_dir = build_cache_root.joinpath(module_name)
            else:
                module_dir = path(module_dir).expand()
            module_dir.makedirs_p()
            success = self.builder(compile_result.c_file, build_dir=module_dir,
                                   **distutils_kwargs)
            if not success:
                raise RuntimeError('Error building extension: %s' %
                                   compile_result.c_file)
            else:
                if module_dir not in sys.path:
                    sys.path.insert(0, module_dir)
        except:
            print build_dir
            raise
        else:
            build_dir.rmtree()
        return module_dir, module_name

    def inline_pyx_module(self, source, **kwargs):
        # Since `functools32.lru_cache` requires all arguments to be hashable,
        # we attempt to transform `list` and `dict` arguments *(which are not
        # hashable)* into hashable types.

        def transform_hashable(args):
            k, v = args
            if isinstance(v, list):
                v = HList(v)
            elif isinstance(v, dict):
                v = HDict(v)
            return k, v

        kwargs_hdict = HDict(map(transform_hashable, kwargs.items()))
        return self._inline_pyx_module(source, kwargs_hdict)

    @functools32.lru_cache()
    # Cache calls since they will result in the same compiled code.
    def _inline_pyx_module(self, source, kwargs_hdict):
        source_file = path(tempfile.mktemp(prefix='cythrust__', suffix='.pyx'))

        try:
            with source_file.open('wb') as output:
                output.write(source)
            return self.build_pyx(source_file, module_name='inline_%s' %
                                  source_file.read_hexhash('sha1'),
                                  **kwargs_hdict)
        finally:
            source_file.remove()
