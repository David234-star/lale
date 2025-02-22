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

import autoai_libs.cognito.transforms.transform_utils

import lale.docstrings
import lale.operators

from ._common_schemas import (
    _hparams_apply_all,
    _hparams_datatypes,
    _hparams_feat_constraints,
    _hparams_tans_class,
    _hparams_tgraph,
    _hparams_transformer_name,
)


class _TB2Impl:
    def __init__(
        self,
        tans_class,
        name,
        datatypes1,
        feat_constraints1,
        datatypes2,
        feat_constraints2,
        tgraph=None,
        apply_all=True,
    ):
        self._hyperparams = {
            "tans_class": tans_class,
            "name": name,
            "datatypes1": datatypes1,
            "feat_constraints1": feat_constraints1,
            "datatypes2": datatypes2,
            "feat_constraints2": feat_constraints2,
            "tgraph": tgraph,
            "apply_all": apply_all,
        }
        self._wrapped_model = autoai_libs.cognito.transforms.transform_utils.TB2(
            **self._hyperparams
        )

    def fit(self, X, y=None):
        self._wrapped_model.fit(X, y)
        return self

    def transform(self, X):
        result = self._wrapped_model.transform(X)
        return result


_hyperparams_schema = {
    "allOf": [
        {
            "description": "This first object lists all constructor arguments with their types, but omits constraints for conditional hyperparameters.",
            "type": "object",
            "additionalProperties": False,
            "required": [
                "tans_class",
                "name",
                "datatypes1",
                "feat_constraints1",
                "datatypes2",
                "feat_constraints2",
                "tgraph",
                "apply_all",
            ],
            "relevantToOptimizer": [],
            "properties": {
                "tans_class": _hparams_tans_class,
                "name": _hparams_transformer_name,
                "datatypes1": _hparams_datatypes(
                    description="List of datatypes that are valid input (first argument) to the transformer function (numeric, float, int, etc.)."
                ),
                "feat_constraints1": _hparams_feat_constraints(
                    description="All constraints that must be satisfied by a column to be considered a valid input (first argument) to this transform."
                ),
                "datatypes2": _hparams_datatypes(
                    description="List of datatypes that are valid input (second argument) to the transformer function (numeric, float, int, etc.)."
                ),
                "feat_constraints2": _hparams_feat_constraints(
                    description="All constraints that must be satisfied by a column to be considered a valid input (second argument) to this transform."
                ),
                "tgraph": _hparams_tgraph,
                "apply_all": _hparams_apply_all,
            },
        }
    ]
}

_input_fit_schema = {
    "type": "object",
    "required": ["X"],
    "additionalProperties": False,
    "properties": {
        "X": {
            "type": "array",
            "items": {"type": "array", "items": {"laleType": "Any"}},
        },
        "y": {"laleType": "Any"},
    },
}

_input_transform_schema = {
    "type": "object",
    "required": ["X"],
    "additionalProperties": False,
    "properties": {
        "X": {"type": "array", "items": {"type": "array", "items": {"laleType": "Any"}}}
    },
}

_output_transform_schema = {
    "description": "Features; the outer array is over samples.",
    "type": "array",
    "items": {"type": "array", "items": {"laleType": "Any"}},
}

_combined_schemas = {
    "$schema": "http://json-schema.org/draft-04/schema#",
    "description": """Operator from `autoai_libs`_. Feature transformation for binary state-based transformations (with fit/transform), such as group-by.

.. _`autoai_libs`: https://pypi.org/project/autoai-libs""",
    "documentation_url": "https://lale.readthedocs.io/en/latest/modules/lale.lib.autoai_libs.tb2.html",
    "import_from": "autoai_libs.cognito.transforms.transform_utils",
    "type": "object",
    "tags": {"pre": [], "op": ["transformer"], "post": []},
    "properties": {
        "hyperparams": _hyperparams_schema,
        "input_fit": _input_fit_schema,
        "input_transform": _input_transform_schema,
        "output_transform": _output_transform_schema,
    },
}


TB2 = lale.operators.make_operator(_TB2Impl, _combined_schemas)

lale.docstrings.set_docstrings(TB2)
