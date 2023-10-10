from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name='amodevices',
    version='0.1',
    author='Lothar Maisenbacher',
    author_email='lothar.maisenbacher@berkeley.edu',
    description='Drivers for AMO (atomic, molecular, and optical physics) laboratory devices.',
    long_description=long_description,
    long_description_content_type="text/markdown",
    url='https://github.com/lmaisenbacher/amodevices',
    packages=[
        'amodevices',
        'amodevices.thorlabs_k10cr1',
        'amodevices.thorlabs_kpa101',
        'amodevices.thorlabs_mdt693b'
        ],
    install_requires=[
        'numpy',
        'pyvisa',
        'pyserial',
        'pylablib'
        ],
)
