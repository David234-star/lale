# Copyright 2019-2023 IBM Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import os
from datetime import datetime

from setuptools import find_packages, setup

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())

try:
    import builtins

    # This trick is borrowed from scikit-learn
    # This is a bit (!) hackish: we are setting a global variable so that the
    # main lale __init__ can detect if it is being loaded by the setup
    # routine, to avoid attempting to import components before installation.
    builtins.__LALE_SETUP__ = True  # type: ignore
except ImportError:
    pass

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

on_rtd = os.environ.get("READTHEDOCS") == "True"
if on_rtd:
    install_requires = []
else:
    install_requires = [
        "numpy<1.24",
        "black>=22.1.0",
        "click==8.0.4",
        "graphviz",
        "hyperopt>=0.2,<=0.2.5",
        "jsonschema",
        "jsonsubschema>=0.0.6",
        "scikit-learn>=1.0.0,<=1.2.0",
        "scipy<1.11.0",
        "pandas<2.0.0",
        "packaging",
        "decorator",
        "astunparse",
        "typing-extensions",
    ]

import lale  # noqa: E402  # pylint:disable=wrong-import-position

if "TRAVIS" in os.environ:
    now = datetime.now().strftime("%y%m%d%H%M")
    VERSION = f"{lale.__version__}-{now}"
else:
    VERSION = lale.__version__

extras_require = {
    "full": [
        "xgboost<=1.5.1",
        "lightgbm",
        "snapml>=1.7.0rc3,<1.12.0",
        "liac-arff>=2.4.0",
        "tensorflow>=2.4.0",
        "smac<=0.10.0",
        "numba",
        "aif360>=0.4.0",
        "protobuf<=3.20.1",
        "torch>=1.0",
        "BlackBoxAuditing",
        "imbalanced-learn",
        "cvxpy>=1.0",
        "fairlearn",
        "h5py",
    ],
    "dev": ["pre-commit"],
    "test": [
        "joblib",
        "ipython<8.8.0",
        "jupyter",
        "sphinx>=5.0.0",
        "sphinx_rtd_theme>=0.5.2",
        "docutils<0.17",
        "m2r2",
        "sphinxcontrib.apidoc",
        "sphinxcontrib-svg2pdfconverter",
        "pytest",
        "pyspark",
        "func_timeout",
        "category-encoders",
        "pynisher==0.6.4",
    ],
    "fairness": [
        "liac-arff>=2.4.0",
        "aif360<0.6.0",
        "imbalanced-learn",
        "protobuf<=3.20.1",
        "BlackBoxAuditing",
    ],
    "tutorial": [
        "ipython<8.8.0",
        "jupyter",
        "xgboost<=1.5.1",
        "imbalanced-learn",
        "liac-arff>=2.4.0",
        "aif360==0.5.0",
        "protobuf<=3.20.1",
        "BlackBoxAuditing",
        "typing-extensions",
    ],
}

classifiers = [
    "Development Status :: 5 - Production/Stable",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: Apache Software License",
    "Operating System :: MacOS",
    "Operating System :: Microsoft :: Windows",
    "Operating System :: POSIX",
    "Operating System :: Unix",
    "Programming Language :: Python",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.7",
    "Programming Language :: Python :: 3.8",
    "Programming Language :: Python :: 3.9",
    "Programming Language :: Python :: 3.10",
    "Topic :: Software Development",
    "Topic :: Scientific/Engineering",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
]

setup(
    name="lale",
    version=VERSION,
    author="Guillaume Baudart, Martin Hirzel, Kiran Kate, Parikshit Ram, Avraham Shinnar",
    description="Library for Semi-Automated Data Science",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/IBM/lale",
    python_requires=">=3.6",
    package_data={"lale": ["py.typed"]},
    packages=find_packages(),
    license="Apache License 2.0",
    classifiers=classifiers,
    install_requires=install_requires,
    extras_require=extras_require,
)
