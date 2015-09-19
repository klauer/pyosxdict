#!/usr/bin/env python

from setuptools import setup

setup(name='pyosxdict',
      version='0.1',
      description='OSX Dictionary Reader',
      author='K Lauer',
      url='https://github.com/klauer/pyosxdict',
      packages=['osxdict', ],
      install_requires=['six>=1.8', 'lxml>=3.3'],
      )
