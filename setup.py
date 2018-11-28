from setuptools import setup, find_packages

setup(
    name='weaver',
    version='0.1.0',
    author='Ben Stevens',
    author_email='benjamin.stevens.au@gmail.com',
    description='Easily manage qemu-based virtual machines and virtual bridges connecting them',
    packages=find_packages(),
    install_requires=["scapy", "pexpect", "backoff"],
)
