from setuptools import setup, find_packages

setup(
    name='ITI1480A-linux',
    description='Tools for the ITI1480A usb protocol analyser',
    keywords='usb protocol analyser ITI1480A',
    version='1.1',
    author='Vincent Pelletier',
    author_email='plr.vincent@gmail.com',
    url='http://github.com/vpelletier/ITI1480A-linux',
    license='GPL 2+',
    platforms=['any'],
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'spt2hex=iti1480a.spt2hex:main',
            'iti1485a-capture=iti1480a.capture:main',
            'iti1485a-display=iti1480a.display:main',
        ],
    },
    classifiers=[
        'Intended Audience :: Information Technology',
        'License :: OSI Approved :: GNU General Public License (GPL)',
        'Operating System :: OS Independent',
    ],
    requires=[
        'libusb1',
        'ply',
    ],
)

