# Copyright 2020 IBM Corporation
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

import autoai_libs.transformers.exportable

import lale.docstrings
import lale.operators

from ._common_schemas import _hparam_activate_flag_unmodified, _hparam_dtypes_list


class _CompressStringsImpl:
    def __init__(self, **hyperparams):
        self._wrapped_model = autoai_libs.transformers.exportable.CompressStrings(
            **hyperparams
        )

    def fit(self, X, y=None):
        self._wrapped_model.fit(X, y)
        return self

    def transform(self, X):
        return self._wrapped_model.transform(X)


_hyperparams_schema = {
    "allOf": [
        {
            "description": "This first object lists all constructor arguments with their types, but omits constraints for conditional hyperparameters.",
            "type": "object",
            "additionalProperties": False,
            "required": [
                "compress_type",
                "dtypes_list",
                "misslist_list",
                "missing_values_reference_list",
                "activate_flag",
            ],
            "relevantToOptimizer": ["compress_type", "activate_flag"],
            "properties": {
                "compress_type": {
                    "description": "Type of string compression: `string` for removing spaces from a string and `hash` for creating an int hash, used when there are columns with strings and cat_imp_strategy=`most_frequent`.",
                    "enum": ["string", "hash"],
                    "default": "string",
                },
                "dtypes_list": _hparam_dtypes_list,
                "misslist_list": {
                    "anyOf": [
                        {
                            "description": "List containing lists of missing values of each column of the input numpy array X.",
                            "type": "array",
                            "items": {"type": "array", "items": {"laleType": "Any"}},
                        },
                        {
                            "description": "If None, the missing values of each column are discovered.",
                            "enum": [None],
                        },
                    ],
                    "default": None,
                },
                "missing_values_reference_list": {
                    "anyOf": [
                        {
                            "description": "Reference list of missing values in the input numpy array X.",
                            "type": "array",
                            "items": {"laleType": "Any"},
                        },
                        {
                            "description": "If None, the missing values of each column are discovered.",
                            "enum": [None],
                        },
                    ],
                    "default": None,
                },
                "activate_flag": _hparam_activate_flag_unmodified,
            },
        }
    ]
}

_input_fit_schema = {
    "type": "object",
    "required": ["X"],
    "additionalProperties": False,
    "properties": {
        "X": {  # Handles 1-D arrays as well
            "anyOf": [
                {"type": "array", "items": {"laleType": "Any"}},
                {
                    "type": "array",
                    "items": {"type": "array", "items": {"laleType": "Any"}},
                },
            ]
        },
        "y": {"laleType": "Any"},
    },
}

_input_transform_schema = {
    "type": "object",
    "required": ["X"],
    "additionalProperties": False,
    "properties": {
        "X": {  # Handles 1-D arrays as well
            "anyOf": [
                {"type": "array", "items": {"laleType": "Any"}},
                {
                    "type": "array",
                    "items": {"type": "array", "items": {"laleType": "Any"}},
                },
            ]
        }
    },
}

_output_transform_schema = {
    "description": "Features; the outer array is over samples.",
    "anyOf": [
        {"type": "array", "items": {"laleType": "Any"}},
        {"type": "array", "items": {"type": "array", "items": {"laleType": "Any"}}},
    ],
}

_combined_schemas = {
    "$schema": "http://json-schema.org/draft-04/schema#",
    "description": """Operator from `autoai_libs`_. Removes spaces and special characters from string columns of a numpy array.

.. _`autoai_libs`: https://pypi.org/project/autoai-libs""",
    "documentation_url": "https://lale.readthedocs.io/en/latest/modules/lale.lib.autoai_libs.compress_strings.html",
    "import_from": "autoai_libs.transformers.exportable",
    "type": "object",
    "tags": {"pre": [], "op": ["transformer"], "post": []},
    "properties": {
        "hyperparams": _hyperparams_schema,
        "input_fit": _input_fit_schema,
        "input_transform": _input_transform_schema,
        "output_transform": _output_transform_schema,
    },
}


CompressStrings = lale.operators.make_operator(_CompressStringsImpl, _combined_schemas)

lale.docstrings.set_docstrings(CompressStrings)
