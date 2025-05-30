from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name='amodevices',
    version='0.1.5',
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
        'amodevices.thorlabs_mdt693b',
        'amodevices.thorlabs_pm100',
        'amodevices.rigol_rsa3000',
        'amodevices.hvs_controller',
        'amodevices.kjlc_xcg',
        'amodevices.keysight_53220a',
        'amodevices.srs_sim922',
        'amodevices.flir_boson',
        'amodevices.thorlabs_bc',
        'amodevices.rigol_dg900pro',
        'amodevices.siglent_ssa3000xplus',
        'amodevices.rp_lockbox',
    ],
    install_requires=[
        'numpy',
        'pyvisa',
        'pyserial',
        'pylablib',
        'PyDAQmx',
        'opencv-python',
    ],
)
